# -*- coding: utf-8 -*-

import numpy as np

from math import ceil
from flightdatautilities.geometry import midpoint

from analysis_engine.settings import (ACCEL_LAT_OFFSET_LIMIT,
                                      ACCEL_LON_OFFSET_LIMIT,
                                      ACCEL_NORM_OFFSET_LIMIT,
                                      CLIMB_OR_DESCENT_MIN_DURATION,
                                      CONTROL_FORCE_THRESHOLD,
                                      FEET_PER_NM,
                                      GRAVITY_METRIC,
                                      HYSTERESIS_FPALT,
                                      KTS_TO_MPS,
                                      NAME_VALUES_CONF,
                                      NAME_VALUES_ENGINE,
                                      NAME_VALUES_FLAP,
                                      SPOILER_DEPLOYED)

from analysis_engine.flight_phase import scan_ils

from analysis_engine.node import KeyPointValueNode, KPV, KTI, P, S, A, M, App

from analysis_engine.library import (ambiguous_runway,
                                     all_of,
                                     any_of,
                                     bearings_and_distances,
                                     bump,
                                     clip,
                                     closest_unmasked_value,
                                     clump_multistate,
                                     coreg,
                                     cycle_counter,
                                     cycle_finder,
                                     cycle_select,
                                     find_edges,
                                     find_edges_on_state_change,
                                     hysteresis,
                                     index_at_value,
                                     index_of_first_start,
                                     index_of_last_stop,
                                     integrate,
                                     is_index_within_slice,
                                     mask_inside_slices,
                                     mask_outside_slices,
                                     max_abs_value,
                                     max_continuous_unmasked,
                                     max_value,
                                     min_value,
                                     repair_mask,
                                     np_ma_masked_zeros_like,
                                     peak_curvature,
                                     rate_of_change_array,
                                     runs_of_ones,
                                     runway_deviation,
                                     runway_distance_from_end,
                                     runway_heading,
                                     shift_slice,
                                     shift_slices,
                                     slice_duration,
                                     slice_midpoint,
                                     slice_samples,
                                     slices_and_not,
                                     slices_from_to,
                                     slices_not,
                                     slices_overlap,
                                     slices_and,
                                     slices_remove_small_slices,
                                     trim_slices,
                                     valid_slices_within_array,
                                     value_at_index,
                                     vspeed_lookup,
                                     vstack_params,
                                     vstack_params_where_state)


##############################################################################
# Superclasses


class FlapOrConfigurationMaxOrMin(object):
    '''
    Abstract superclass.
    '''
    def flap_or_conf_max_or_min(self, conflap, parameter, function, scope=None,
                                include_zero=False):
        '''
        Generic flap and conf key point value creation process.

        :param conflap: conf or flap data, restricted to detent settings.
        :type conflap: parameter (conf = float or flap = degrees or %)
        :param parameter: parameter to be measured at flap/conf detent.
        :type parameter: parameter
        :param function: function to be applied to the parameter values
        :type function: function ('max_value' or 'min_value')
        :param scope: Periods to restrict period to be monitored. Essential for
            minimum speed checks, otherwise all the results relate to taxi
            periods! (Optional)
        :type scope: list of slice
        :param include_zero: option to include zero flap settings. Used for
            monitoring AOA with clean configuration. (Default: False)
        :type include_zero: boolean
        :returns: Nothing as KPVs are created within the routine.
        '''
        if scope == []:
            return  # Can't have an event if the scope is empty.

        if scope:
            scope_array = np_ma_masked_zeros_like(parameter.array)
            for valid in scope:
                scope_array.mask[
                    int(valid.slice.start or 0):
                    int(valid.slice.stop or len(scope_array)) + 1] = False

        kpvs = []
        
        if hasattr(conflap, 'values_mapping'):
            # Multistate
            conflap_detents = conflap.values_mapping.keys()
        else:
            conflap_detents = np.ma.unique(conflap.array)
        
        for detent in conflap_detents:
            if np.ma.is_masked(detent):
                continue
            if detent == 0.0 and include_zero == False:
                continue

            p_with_conflap = np.ma.copy(parameter.array)
            p_with_conflap.mask = np.ma.mask_or(parameter.array.mask,
                                                conflap.array.mask)
            p_with_conflap[conflap.array != detent] = np.ma.masked
            if scope:
                p_with_conflap.mask = np.ma.mask_or(p_with_conflap.mask,
                                                    scope_array.mask)

            # TODO: Check logical or is sensible for all values. (Probably fine
            #       as airspeed will always be higher than max flap setting!)
            index, value = function(p_with_conflap)

            # Check we have a result to record. Note that most flap settings
            # will not be used in the climb, hence this is normal operation.
            if not index or not value:
                continue

            # Ensure KPVs with integer detents don't have decimal places and
            # that those that are floats only have one decimal place:
            detent = int(detent) if float(detent).is_integer() else '%.1f' % detent
            kpvs.append((index, value, detent))
        return kpvs


##############################################################################
# Helpers


def thrust_reversers_working(landing, pwr, tr):
    '''
    Thrust reversers are deployed and average N1 over 65%.
    '''
    high_power = np.ma.masked_less(pwr.array[landing.slice], 65.0)
    high_power_slices = np.ma.clump_unmasked(high_power)
    return clump_multistate(tr.array, 'Deployed', high_power_slices)



##############################################################################
# Acceleration


########################################
# Acceleration: Lateral


class AccelerationLateralMax(KeyPointValueNode):
    '''
    This KPV has no inherent flight phase associated with it, but we can
    reasonably say that we are not interested in anything while the aircraft is
    stationary.
    '''

    units = 'g'

    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Lateral Offset Removed' in available

    def derive(self,
               acc_lat=P('Acceleration Lateral Offset Removed'),
               gnd_spd=P('Groundspeed')):

        if gnd_spd:
            self.create_kpvs_within_slices(
                acc_lat.array,
                gnd_spd.slices_above(5),
                max_abs_value,
            )
        else:
            self.create_kpv(*max_abs_value(acc_lat.array))


class AccelerationLateralAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral Offset Removed'),
               touchdowns=KTI('Touchdown')):

        for touchdown in touchdowns:
            self.create_kpv(*bump(acc_lat, touchdown))


class AccelerationLateralDuringTakeoffMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Lateral)"
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral Offset Removed'),
               takeoff_rolls=S('Takeoff Roll')):

        self.create_kpvs_within_slices(
            acc_lat.array,
            takeoff_rolls,
            max_abs_value,
        )


class AccelerationLateralDuringLandingMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral)."
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral Offset Removed'),
               landing_rolls=S('Landing Roll'),
               ldg_rwy=A('FDR Landing Runway')):

        if ambiguous_runway(ldg_rwy):
            return
        self.create_kpv_from_slices(
            acc_lat.array,
            landing_rolls,
            max_abs_value,
        )


class AccelerationLateralWhileTaxiingStraightMax(KeyPointValueNode):
    '''
    Lateral acceleration while not turning is rarely an issue, so we compute
    only one KPV for taxi out and one for taxi in. The straight sections are
    identified by masking the turning phases and then testing the resulting
    data.
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral Smoothed'),
               taxiing=S('Taxiing'),
               turns=S('Turning On Ground')):

        acc_lat_array = mask_inside_slices(acc_lat.array, turns.get_slices())
        self.create_kpvs_within_slices(acc_lat_array, taxiing, max_abs_value)


class AccelerationLateralWhileTaxiingTurnMax(KeyPointValueNode):
    '''
    Lateral acceleration while taxiing normally occurs in turns, and leads to
    wear on the undercarriage and discomfort for passengers. In extremis this
    can lead to taxiway excursions. Lateral acceleration is used in preference
    to groundspeed as this parameter is available on older aircraft and is
    directly related to comfort.
    
    We use the smoothed lateral acceleration which removes spikey signals due
    to uneven surfaces.
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral Smoothed'),
               taxiing=S('Taxiing'),
               turns=S('Turning On Ground')):

        acc_lat_array = mask_outside_slices(acc_lat.array, turns.get_slices())
        self.create_kpvs_within_slices(acc_lat_array, taxiing, max_abs_value)


class AccelerationLateralOffset(KeyPointValueNode):
    '''
    This KPV computes the lateral accelerometer datum offset, as for
    AccelerationNormalOffset. The more complex slicing statement ensures we
    only accumulate error estimates when taxiing in a straight line.
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral'),
               taxiing=S('Taxiing'),
               turns=S('Turning On Ground')):

        total_sum = 0.0
        total_count = 0
        straights = slices_and(
            [s.slice for s in list(taxiing)],
            slices_not([s.slice for s in list(turns)]),
        )
        for straight in straights:
            unmasked_data = np.ma.compressed(acc_lat.array[straight])
            count = len(unmasked_data)
            if count:
                total_count += count
                total_sum += np.sum(unmasked_data)
        if total_count > 20:
            delta = total_sum / float(total_count)
            if abs(delta) < ACCEL_LAT_OFFSET_LIMIT:
                self.create_kpv(0, delta)


########################################
# Acceleration: Longitudinal


class AccelerationLongitudinalOffset(KeyPointValueNode):
    '''
    This KPV computes the longitudinal accelerometer datum offset, as for
    AccelerationNormalOffset. We use all the taxiing phase and assume that
    the accelerations and decelerations will roughly balance out over the
    duration of the taxi phase.
    '''

    units = 'g'

    def derive(self,
               acc_lat=P('Acceleration Lateral'),
               taxiing=S('Taxiing')):

        total_sum = 0.0
        total_count = 0
        taxis = [s.slice for s in list(taxiing)]
        for taxi in taxis:
            unmasked_data = np.ma.compressed(acc_lat.array[taxi])
            count = len(unmasked_data)
            if count:
                total_count += count
                total_sum += np.sum(unmasked_data)
        if total_count > 20:
            delta = total_sum / float(total_count)
            if abs(delta) < ACCEL_LON_OFFSET_LIMIT:
                self.create_kpv(0, delta)


class AccelerationLongitudinalDuringTakeoffMax(KeyPointValueNode):
    '''
    This may be of interest where takeoff performance is an issue, though not
    normally monitored as a safety event.
    '''

    units = 'g'

    def derive(self,
               acc_lon=P('Acceleration Longitudinal'),
               takeoff=S('Takeoff')):

        self.create_kpv_from_slices(acc_lon.array, takeoff, max_value)


class AccelerationLongitudinalDuringLandingMin(KeyPointValueNode):
    '''
    This is an indication of severe braking and/or use of reverse thrust or
    reverse pitch.
    '''

    units = 'g'

    def derive(self,
               acc_lon=P('Acceleration Longitudinal'),
               landing=S('Landing')):

        self.create_kpv_from_slices(acc_lon.array, landing, min_value)


########################################
# Acceleration: Normal


class AccelerationNormalMax(KeyPointValueNode):
    '''
    This KPV has no inherent flight phase associated with it, but we can
    reasonably say that we are not interested in anything while the aircraft is
    stationary.
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               mobile=S('Mobile')):

        self.create_kpv_from_slices(acc_norm.array, mobile, max_value)


class AccelerationNormal20FtToFlareMax(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            acc_norm.array,
            alt_aal.slices_from_to(20, 5),
            max_value,
        )


class AccelerationNormalWithFlapUpWhileAirborneMax(KeyPointValueNode):
    '''
    '''

    units = 'g'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Acceleration Normal Offset Removed', 'Airborne'),
                      available)

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        # Mask data where the flaps are down:
        acc_flap_up = np.ma.masked_where(flap.array > 0.0, acc_norm.array)
        self.create_kpv_from_slices(acc_flap_up, airborne, max_value)


class AccelerationNormalWithFlapUpWhileAirborneMin(KeyPointValueNode):
    '''
    '''

    units = 'g'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Acceleration Normal Offset Removed', 'Airborne'),
                      available)

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        # Mask data where the flaps are down:
        acc_flap_up = np.ma.masked_where(flap.array > 0.0, acc_norm.array)
        self.create_kpv_from_slices(acc_flap_up, airborne, min_value)


class AccelerationNormalWithFlapDownWhileAirborneMax(KeyPointValueNode):
    '''
    '''

    units = 'g'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Acceleration Normal Offset Removed', 'Airborne'),
                      available)

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        # Mask data where the flaps are up:
        acc_flap_up = np.ma.masked_where(flap.array == 0.0, acc_norm.array)
        self.create_kpv_from_slices(acc_flap_up, airborne, max_value)


class AccelerationNormalWithFlapDownWhileAirborneMin(KeyPointValueNode):
    '''
    '''

    units = 'g'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Acceleration Normal Offset Removed', 'Airborne'),
                      available)

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        # Mask data where the flaps are up:
        acc_flap_up = np.ma.masked_where(flap.array == 0.0, acc_norm.array)
        self.create_kpv_from_slices(acc_flap_up, airborne, min_value)


class AccelerationNormalAtLiftoff(KeyPointValueNode):
    '''
    This is a measure of the normal acceleration at the point of liftoff, and
    is related to the pitch rate at takeoff.
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               liftoffs=KTI('Liftoff')):

        for liftoff in liftoffs:
            self.create_kpv(*bump(acc_norm, liftoff))


class AccelerationNormalAtTouchdown(KeyPointValueNode):
    '''
    This is the peak acceleration at landing, often used to identify hard
    landings for maintenance purposes.
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               touchdowns=KTI('Touchdown')):

        for touchdown in touchdowns:
            self.create_kpv(*bump(acc_norm, touchdown))


class AccelerationNormalTakeoffMax(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal Offset Removed'),
               takeoffs=S('Takeoff')):

        self.create_kpvs_within_slices(acc_norm.array, takeoffs, max_value)


class AccelerationNormalOffset(KeyPointValueNode):
    '''
    This KPV computes the normal accelerometer datum offset. This allows for
    offsets that are sometimes found in these sensors which remain in service
    although outside the permitted accuracy of the signal.
    '''

    units = 'g'

    def derive(self,
               acc_norm=P('Acceleration Normal'),
               taxiing=S('Taxiing')):

        total_sum = 0.0
        total_count = 0
        for taxi in taxiing:
            unmasked_data = np.ma.compressed(acc_norm.array[taxi.slice])
            count = len(unmasked_data)
            if count:
                total_count += count
                total_sum += np.sum(unmasked_data)
        if total_count > 20:
            delta = total_sum / float(total_count) - 1.0
            if abs(delta) < ACCEL_NORM_OFFSET_LIMIT:
                self.create_kpv(0, delta + 1.0)


##############################################################################
# Airspeed


########################################
# Airspeed: General


class AirspeedMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(air_spd.array, airborne, max_value)


class AirspeedAt8000FtDescending(KeyPointValueNode):
    '''
    Refactor to be a formatted name node if multiple Airspeed At Altitude
    KPVs are required. Could depend on either Altitude When Climbing or
    Altitude When Descending, but the assumption is that we'll have both.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std_desc=S('Altitude When Descending')):
        
        self.create_kpvs_at_ktis(air_spd.array,
                                 alt_std_desc.get(name='8000 Ft Descending'))


class AirspeedDuringCruiseMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               cruises=S('Cruise')):

        self.create_kpvs_within_slices(air_spd.array, cruises, max_value)


class AirspeedDuringCruiseMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               cruises=S('Cruise')):

        self.create_kpvs_within_slices(air_spd.array, cruises, min_value)


class AirspeedGustsDuringFinalApproach(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    Excursions - Landing (Lateral). Gusts during flare/final approach. This
    is tricky. Try Speed variation >15kt 30RA to 10RA. KPV looks at peak to
    peak values to get change in airspeed. Event uses interpolated RALT
    samples and looks at the airspeed samples that fall between RALT = 30ft
    and 10ft. DW suggested that the airspeed samples should also be
    interpolated in order to be able to estimate airspeed as to close to the
    ends of the RALT range as possible.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               gnd_spd=P('Groundspeed'),
               alt_rad=P('Altitude Radio'),
               airborne=S('Airborne')):

        _, fin_apps = slices_from_to(alt_rad.array, 30, 10)
        descents = slices_and([s.slice for s in airborne], fin_apps)
        for descent in descents:
            # Ensure we encompass the range of interest.
            scope = slice(descent.start - 5, descent.stop + 5)
            # We'd like to use groundspeed to compute the wind gust, but
            # variations in airspeed are a suitable backstop.
            if gnd_spd:
                headwind = air_spd.array[scope] - gnd_spd.array[scope]
            else:
                headwind = air_spd.array[scope] - air_spd.array[scope][0]
            # Precise indexing is used as this is only a short segment. Note
            # that the _idx values are floating point interpolations of the
            # radio altimeter signal, and the headwind array is also
            # interpolated.
            idx_start = index_at_value(alt_rad.array, 30.0, scope)
            idx_stop = index_at_value(alt_rad.array, 10.0, scope)
            
            # This condition can arise in some corrupt data cases, or for a
            # go-around with a minimum between 30ft and 10ft.
            if idx_start is None or idx_stop is None:
                continue

            new_app = shift_slice(descent, -scope.start)
            peak = max_value(headwind, new_app,
                    start_edge=idx_start - scope.start,
                    stop_edge=idx_stop - scope.start)
            trough = min_value(headwind, new_app,
                    start_edge=idx_start - scope.start,
                    stop_edge=idx_stop - scope.start)
            if peak.value and trough.value:
                value = peak.value - trough.value
                index = ((peak.index + trough.index) / 2.0) + scope.start
                self.create_kpv(index, value)


########################################
# Airspeed: Climbing


class AirspeedAtLiftoff(KeyPointValueNode):
    '''
    A 'Tailwind At Liftoff' KPV would complement this KPV when used for 'Speed
    high at takeoff' events.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(air_spd.array, liftoffs)


class AirspeedAt35FtDuringTakeoff(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               takeoffs=S('Takeoff')):

        for takeoff in takeoffs:
            index = takeoff.stop_edge  # Takeoff ends at 35ft!
            value = value_at_index(air_spd.array, index)
            self.create_kpv(index, value)


class Airspeed35To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               initial_climb=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 35, 1000)
        alt_climb_sections = valid_slices_within_array(alt_band, initial_climb)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_climb_sections,
            max_value)


class Airspeed35To1000FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               initial_climb=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 35, 1000)
        alt_climb_sections = valid_slices_within_array(alt_band, initial_climb)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_climb_sections,
            min_value,
        )


class Airspeed1000To8000FtMax(KeyPointValueNode):
    '''
    Airspeed from 1000ft above the airfield to a pressure altitude of 8000ft.
    As we are only interested in the climbing phase, this is used as the
    normal slices_from_to will not work with two parameters.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               alt_std=P('Altitude STD Smoothed'),
               climbs=S('Climb')):

        for climb in climbs:
            aal=np.ma.clump_unmasked(np.ma.masked_less(alt_aal.array[climb.slice], 1000.0))
            std=np.ma.clump_unmasked(np.ma.masked_greater(alt_std.array[climb.slice], 8000.0))
            scope=shift_slices(slices_and(aal, std), climb.slice.start)
            self.create_kpv_from_slices(
                air_spd.array,
                scope,
                max_value
                )


class Airspeed8000To10000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               climb=S('Climb')):

        alt_band = np.ma.masked_outside(alt_std.array, 8000, 10000)
        alt_climb_sections = valid_slices_within_array(alt_band, climb)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_climb_sections,
            max_value,
        )


########################################
# Airspeed: Descending


class Airspeed10000To8000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               descent=S('Descent')):

        alt_band = np.ma.masked_outside(alt_std.array, 10000, 8000)
        alt_descent_sections = valid_slices_within_array(alt_band, descent)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_descent_sections,
            max_value,
        )


class Airspeed8000To5000FtMax(KeyPointValueNode):
    '''
    Airspeed from 8000ft pressure altitude to 5000ft above the airfield.
    As we are only interested in the descending phase, this is used as the
    normal slices_from_to will not work with two parameters.
    '''
    units = 'kt'
     
    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               alt_std=P('Altitude STD Smoothed'),
               descends=S('Descent')):
    
        for descend in descends:
            std=np.ma.clump_unmasked(np.ma.masked_greater(alt_std.array[descend.slice], 8000.0))
            aal=np.ma.clump_unmasked(np.ma.masked_less(alt_aal.array[descend.slice], 5000.0))
            scope=shift_slices(slices_and(aal, std), descend.slice.start)
            self.create_kpv_from_slices(
                air_spd.array,
                scope,
                max_value
                )


class Airspeed5000To3000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               descent=S('Descent')):

        alt_band = np.ma.masked_outside(alt_aal.array, 5000, 3000)
        alt_descent_sections = valid_slices_within_array(alt_band, descent)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_descent_sections,
            max_value,
        )


class Airspeed3000To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               init_app=S('Initial Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 3000, 1000)
        alt_descent_sections = valid_slices_within_array(alt_band, init_app)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_descent_sections,
            max_value,
        )


class Airspeed3000FtToTopOfClimbMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               tocs=KTI('Top Of Climb')):
        toc = tocs.get_first()
        
        if not toc:
            return
        
        index_at_3000ft = index_at_value(alt_aal.array, 3000,
                                         _slice=slice(toc.index, None, -1))
        
        if not index_at_3000ft:
            # Top Of Climb below 3000ft?
            return
        
        self.create_kpv(*max_value(air_spd.array,
                                  _slice=slice(index_at_3000ft, toc.index)))


class Airspeed1000To500FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               final_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 500)
        alt_descent_sections = valid_slices_within_array(alt_band, final_app)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_descent_sections,
            max_value,
        )


class Airspeed1000To500FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               final_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 500)
        alt_descent_sections = valid_slices_within_array(alt_band, final_app)
        self.create_kpvs_within_slices(
            air_spd.array,
            alt_descent_sections,
            min_value,
        )


class Airspeed500To20FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            air_spd.array,
            alt_aal.slices_from_to(500, 20),
            max_value,
        )


class Airspeed500To20FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            air_spd.array,
            alt_aal.slices_from_to(500, 20),
            min_value,
        )


class AirspeedAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(air_spd.array, touchdowns)


class AirspeedTrueAtTouchdown(KeyPointValueNode):
    '''
    This KPV relates to groundspeed at touchdown to illustrate headwinds and
    tailwinds. We also have 'Tailwind 100 Ft To Touchdown Max' to cater for
    safety event triggers.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed True'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(air_spd.array, touchdowns)


class AirspeedReferenceVariationMax(KeyPointValueNode):
    '''
    Maximum difference between recorded/afr values and lookup values.
    '''

    def derive(self,
               spd_ref_rec=P('Airspeed Reference'),
               spd_ref_table=P('Airspeed Reference Lookup'),
               apps=S('Approach And Landing')):

        ref_differences = spd_ref_rec.array - spd_ref_table.array
        self.create_kpv_from_slices(
                    ref_differences,
                    apps.get_slices(),
                    max_abs_value,
                )


class V2VariationMax(KeyPointValueNode):
    '''
    Maximum difference between recorded/afr values and lookup values.
    '''

    def derive(self,
               v2_rec=P('V2'),
               v2_table=P('V2 Lookup')):

        v2_differences = v2_rec.array - v2_table.array
        self.create_kpv_from_slices(
            v2_differences,
            [slice(0, len(v2_rec.array)),],
            max_abs_value)


########################################
# Airspeed: Minus V2


class AirspeedMinusV2AtLiftoff(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 At Liftoff'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(spd_v2.array, liftoffs)


class AirspeedMinusV2At35FtDuringTakeoff(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 At 35 Ft During Takeoff'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2'),
               takeoffs=S('Takeoff')):

        for takeoff in takeoffs:
            index = takeoff.stop_edge  # Takeoff ends at 35ft!
            value = spd_v2.array[index]
            self.create_kpv(index, value)


class AirspeedMinusV235To1000FtMax(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 35 To 1000 Ft Max'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_v2.array,
            alt_aal.slices_from_to(35, 1000),
            max_value,
        )


class AirspeedMinusV235To1000FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 35 To 1000 Ft Min'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_v2.array,
            alt_aal.slices_from_to(35, 1000),
            min_value,
        )


class AirspeedMinusV2For3Sec35To1000FtMax(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 For 3 Sec 35 To 1000 Ft Max'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2 For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):
        
        self.create_kpvs_within_slices(
            spd_v2.array,
            trim_slices(alt_aal.slices_from_to(35, 1000), 3, self.frequency,
                        duration.value),
            max_value,
        )


class AirspeedMinusV2For3Sec35To1000FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Airspeed Minus V2 For 3 Sec 35 To 1000 Ft Min'
    units = 'kt'

    def derive(self,
               spd_v2=P('Airspeed Minus V2 For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            spd_v2.array,
            trim_slices(alt_aal.slices_from_to(35, 1000), 3, self.frequency,
                        duration.value),
            min_value,
        )


########################################
# Airspeed: Relative


class AirspeedRelativeAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(spd_rel.array, touchdowns)


class AirspeedRelative1000To500FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_from_to(1000, 500),
            max_value,
        )


class AirspeedRelative1000To500FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_from_to(1000, 500),
            min_value,
        )


class AirspeedRelative500To20FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_from_to(500, 20),
            max_value,
        )


class AirspeedRelative500To20FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_from_to(500, 20),
            min_value,
        )


class AirspeedRelative20FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_to_kti(20, touchdowns),
            max_value,
        )


class AirspeedRelative20FtToTouchdownMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            alt_aal.slices_to_kti(20, touchdowns),
            min_value,
        )


class AirspeedRelativeFor3Sec1000To500FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):
        
        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_from_to(1000, 500), 3, self.frequency,
                        duration.value),
            max_value,
        )


class AirspeedRelativeFor3Sec1000To500FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):
        
        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_from_to(1000, 500), 3, self.frequency,
                        duration.value),
            min_value,
        )


class AirspeedRelativeFor3Sec500To20FtMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_from_to(500, 20), 3, self.frequency,
                        duration.value),
            max_value,
        )


class AirspeedRelativeFor3Sec500To20FtMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_from_to(500, 20), 3, self.frequency,
                        duration.value),
            min_value,
        )


class AirspeedRelativeFor3Sec20FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_to_kti(20, touchdowns), 3,
                        self.frequency, duration.value),
            max_value,
        )


class AirspeedRelativeFor3Sec20FtToTouchdownMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               spd_rel=P('Airspeed Relative For 3 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            spd_rel.array,
            trim_slices(alt_aal.slices_to_kti(20, touchdowns), 3,
                        self.frequency, duration.value),
            min_value,
        )


class AirspeedMinusMinManeouvringSpeedMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self, spd_rel=P('Airspeed Minus Min Maneouvring Speed'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(spd_rel.array, airborne, min_value)


########################################
# Airspeed: Flap


class AirspeedWithFlapMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''
    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With %(parameter)s %(flap)s Max'
    NAME_VALUES = NAME_VALUES_FLAP.copy()
    NAME_VALUES.update({
        'parameter': [
            'Flap',
            'Flap Including Transition',
            'Flap Excluding Transition',
        ],
    })
    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap', 'Flap Including Transition',
                       'Flap Excluding Transition'), available) and \
               all_of(('Airspeed', 'Fast'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               flap_inc_trans=M('Flap Including Transition'),
               flap_exc_trans=M('Flap Excluding Transition'),
               airspeed=P('Airspeed'),
               scope=S('Fast')):

        # We want to use flap lever detents if they are available, but we need
        # to ensure that the parameter is called flap with the name hack below:
        flap = flap_lever or flap_synth
        if flap:
            flap.name = 'Flap'

        for parameter in (flap, flap_inc_trans, flap_exc_trans):
            if not parameter:
                continue
            # Fast scope traps flap changes very late on the approach and
            # raising flaps before 80 kt on the landing run.
            data = self.flap_or_conf_max_or_min(parameter, airspeed, max_value,
                                                scope=scope)
            for index, value, detent in data:
                self.create_kpv(index, value, parameter=parameter.name,
                                flap=detent)


class AirspeedWithFlapMin(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With Flap %(flap)s Min'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Airspeed', 'Airborne'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airspeed=P('Airspeed'),
               scope=S('Airborne')):
        flap = flap_lever or flap_synth
        # Airborne scope avoids deceleration on the runway "corrupting" the
        # minimum airspeed with landing flap.
        data = self.flap_or_conf_max_or_min(flap, airspeed, min_value,
                                            scope=scope)
        for index, value, detent in data:
            self.create_kpv(index, value, flap=detent)


class AirspeedWithFlapDuringClimbMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''
    
    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With %(parameter)s %(flap)s During Climb Max'
    NAME_VALUES = NAME_VALUES_FLAP.copy()
    NAME_VALUES.update({
        'parameter': [
            'Flap',
            'Flap Including Transition',
            'Flap Excluding Transition',
        ],
    })
    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap', 'Flap Including Transition',
                       'Flap Excluding Transition'), available) and \
               all_of(('Airspeed', 'Climb'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               flap_inc_trans=M('Flap Including Transition'),
               flap_exc_trans=M('Flap Excluding Transition'),
               airspeed=P('Airspeed'),
               scope=S('Climb')):

        # We want to use flap lever detents if they are available, but we need
        # to ensure that the parameter is called flap with the name hack below:
        flap = flap_lever or flap_synth
        if flap:
            flap.name = 'Flap'

        for parameter in (flap, flap_inc_trans, flap_exc_trans):
            if not parameter:
                continue
            # Fast scope traps flap changes very late on the approach and
            # raising flaps before 80 kt on the landing run.
            data = self.flap_or_conf_max_or_min(parameter, airspeed, max_value,
                                                scope=scope)
            for index, value, detent in data:
                self.create_kpv(index, value, parameter=parameter.name,
                                flap=detent)


class AirspeedWithFlapDuringClimbMin(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With Flap %(flap)s During Climb Min'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Airspeed', 'Climb'),
                      available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airspeed=P('Airspeed'),
               scope=S('Climb')):
        flap = flap_lever or flap_synth
        data = self.flap_or_conf_max_or_min(flap, airspeed, min_value,
                                            scope=scope)
        for index, value, detent in data:
            self.create_kpv(index, value, parameter=flap.name,
                            flap=detent)


class AirspeedWithFlapDuringDescentMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With %(parameter)s %(flap)s During Descent Max'
    NAME_VALUES = NAME_VALUES_FLAP.copy()
    NAME_VALUES.update({
        'parameter': [
            'Flap',
            'Flap Including Transition',
            'Flap Excluding Transition',
        ],
    })
    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap', 'Flap Including Transition',
                       'Flap Excluding Transition'), available) and \
               all_of(('Airspeed', 'Descent'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               flap_inc_trans=M('Flap Including Transition'),
               flap_exc_trans=M('Flap Excluding Transition'),
               airspeed=P('Airspeed'),
               scope=S('Descent')):

        # We want to use flap lever detents if they are available, but we need
        # to ensure that the parameter is called flap with the name hack below:
        flap = flap_lever or flap_synth
        if flap:
            flap.name = 'Flap'

        for parameter in (flap, flap_inc_trans, flap_exc_trans):
            if not parameter:
                continue
            # Fast scope traps flap changes very late on the approach and
            # raising flaps before 80 kt on the landing run.
            data = self.flap_or_conf_max_or_min(parameter, airspeed, max_value,
                                                scope=scope)
            for index, value, detent in data:
                self.create_kpv(index, value, parameter=parameter.name,
                                flap=detent)


class AirspeedWithFlapDuringDescentMin(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed With Flap %(flap)s During Descent Min'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Airspeed', 'Descent To Flare'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airspeed=P('Airspeed'),
               scope=S('Descent To Flare')):
        flap = flap_lever or flap_synth
        data = self.flap_or_conf_max_or_min(flap, airspeed, min_value,
                                            scope=scope)
        for index, value, detent in data:
            self.create_kpv(index, value, parameter=flap.name,
                            flap=detent)


class AirspeedRelativeWithFlapDuringDescentMin(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Airspeed Relative With Flap %(flap)s During Descent Min'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Airspeed Relative', 'Descent To Flare'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airspeed=P('Airspeed Relative'),
               scope=S('Descent To Flare')):
        flap = flap_lever or flap_synth
        data = self.flap_or_conf_max_or_min(flap, airspeed, min_value,
                                            scope=scope)
        for index, value, detent in data:
            self.create_kpv(index, value, parameter=flap.name,
                            flap=detent)


class AirspeedAtFirstFlapExtensionWhileAirborne(KeyPointValueNode):
    '''
    Airspeed measured at the point of Flap Extension while airborne.
    '''
    units = 'kt'
    
    def derive(self, airspeed=P('Airspeed'),
               ff_ext=KTI('First Flap Extension While Airborne')):
        if ff_ext:
            index=ff_ext[-1].index
            self.create_kpv(index, value_at_index(airspeed.array, index))


########################################
# Airspeed: Landing Gear


class AirspeedWithGearDownMax(KeyPointValueNode):
    '''
    Maximum airspeed observed while the landing gear down.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               gear=M('Gear Down'),
               airs=S('Airborne')):

        gear.array[gear.array != 'Down'] = np.ma.masked
        gear_downs = np.ma.clump_unmasked(gear.array)
        self.create_kpvs_within_slices(
            air_spd.array, slices_and(airs.get_slices(), gear_downs),
            max_value)


class AirspeedWhileGearRetractingMax(KeyPointValueNode):
    '''
    Maximum airspeed observed while the landing gear was retracting.
    '''
    units = 'kt'

    def derive(self, air_spd=P('Airspeed'),
               gear_ret=S('Gear Retracting')):
        self.create_kpvs_within_slices(air_spd.array, gear_ret, max_value)


class AirspeedWhileGearExtendingMax(KeyPointValueNode):
    '''
    Maximum airspeed observed while the landing gear was extending.
    '''
    units = 'kt'

    def derive(self, air_spd=P('Airspeed'),
               gear_ext=S('Gear Extending')):
        self.create_kpvs_within_slices(air_spd.array, gear_ext, max_value)


class AirspeedAtGearUpSelection(KeyPointValueNode):
    '''
    Airspeed measurment at the point of Gear Up Selection.
    '''
    units = 'kt'

    def derive(self, air_spd=P('Airspeed'),
               gear_up_sel=KTI('Gear Up Selection')):
        self.create_kpvs_at_ktis(air_spd.array, gear_up_sel)


class AirspeedAtGearDownSelection(KeyPointValueNode):
    '''
    Airspeed measurment at the point of Gear Down Selection
    '''
    units = 'kt'

    def derive(self, air_spd=P('Airspeed'),
               gear_dn_sel=KTI('Gear Down Selection')):
        self.create_kpvs_at_ktis(air_spd.array, gear_dn_sel)


class MainGearOnGroundToNoseGearOnGroundDuration(KeyPointValueNode):
    '''
    The time duration between the main gear touching the ground and the nose
    gear touching the ground.
    '''
    units = 's'

    def derive(self, gog=P('Gear On Ground'), gogn=P('Gear (N) On Ground'),
               landings=S('Landing')):

        for landing in landings:
            gog_index = index_of_first_start(gog.array == 'Ground',
                                             landing.slice)
            gogn_index = index_of_first_start(gogn.array == 'Ground',
                                              landing.slice)
            self.create_kpv(gog_index,
                            (gogn_index - gog_index) / self.frequency)


########################################
# Airspeed: Conf


class AirspeedWithConfigurationMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''
    NAME_FORMAT = 'Airspeed With Configuration %(conf)s Max'
    NAME_VALUES = NAME_VALUES_CONF.copy()

    units = 'kt'

    def derive(self,
               conf=M('Configuration'),
               airspeed=P('Airspeed'),
               scope=S('Fast')):

        # Fast scope traps configuration changes very late on the approach and
        # before 80 kt on the landing run.
        data = self.flap_or_conf_max_or_min(conf, airspeed, max_value,
                                            scope=scope)
        for index, value, detent in data:
            conf_setting = conf.values_mapping[detent]
            self.create_kpv(index, value, parameter=conf.name,
                            conf=conf_setting)


class AirspeedRelativeWithConfigurationDuringDescentMin(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    NAME_FORMAT = 'Airspeed Relative With Configuration %(conf)s During Descent Min'
    NAME_VALUES = NAME_VALUES_CONF.copy()

    units = 'kt'

    def derive(self,
               conf=M('Configuration'),
               airspeed=P('Airspeed Relative'),
               scope=S('Descent To Flare')):

        data = self.flap_or_conf_max_or_min(conf, airspeed, min_value, scope=scope)
        for index, value, detent in data:
            conf_setting = conf.values_mapping[detent]
            self.create_kpv(index, value, conf=conf_setting)


########################################
# Airspeed: Speedbrakes


class AirspeedWithSpeedbrakeDeployedMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               spdbrk=P('Speedbrake')):

        spdbrk.array[spdbrk.array > SPOILER_DEPLOYED] = np.ma.masked
        spoiler_deployeds = np.ma.clump_unmasked(spdbrk.array)
        self.create_kpvs_within_slices(
            air_spd.array, spoiler_deployeds, max_value)


########################################
# Airspeed: Thrust Reversers


class AirspeedWithThrustReversersDeployedMin(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed True'),
               tr=M('Thrust Reversers'),
               power=P('Eng (*) N1 Avg'),
               landings=S('Landing')):

        for landing in landings:
            high_rev = thrust_reversers_working(landing, power, tr)
            self.create_kpvs_within_slices(air_spd.array, high_rev, min_value)


class AirspeedAtThrustReversersSelection(KeyPointValueNode):
    '''
    This gives the indicated airspeed where the thrust reversers were selected.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               tr=M('Thrust Reversers'),
               landings=S('Landing')):

        slices = [s.slice for s in landings]
        to_scan = clump_multistate(tr.array, 'Stowed', slices, condition=False)
        self.create_kpv_from_slices(air_spd.array, to_scan, max_value)


########################################
# Airspeed: Other


class AirspeedVacatingRunway(KeyPointValueNode):
    '''
    Airspeed vacating runway uses true airspeed, which is extended below the
    minimum range of the indicated airspeed specifically for this type of
    event. See the derived parameter for details of how groundspeed or
    acceleration data is used to cover the landing phase.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed True'),
               off_rwy=KTI('Landing Turn Off Runway')):

        self.create_kpvs_at_ktis(air_spd.array, off_rwy)


class AirspeedDuringRejectedTakeoffMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               rtos=S('Rejected Takeoff')):

        self.create_kpvs_within_slices(air_spd.array, rtos, max_value)


class AirspeedBelow10000FtDuringDescentMax(KeyPointValueNode):
    '''
    Outside the USA 10,000 ft relates to flight levels, whereas FAA regulations
    (and possibly others we don't currently know about) relate to height above
    sea level (QNH) hence the options based on landing airport location.

    In either case, we apply some hysteresis to prevent nuisance retriggering
    which can arise if the aircraft is sitting on the 10,000ft boundary.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               alt_qnh=P('Altitude QNH'),
               ldg_apt=A('FDR Landing Airport'),
               descent=S('Descent')):

        country = None
        if ldg_apt.value:
            country = ldg_apt.value.get('location', {}).get('country')

        alt = alt_qnh.array if country == 'United States' else alt_std.array
        alt = hysteresis(alt, HYSTERESIS_FPALT)

        height_bands = np.ma.clump_unmasked(np.ma.masked_greater(alt, 10000))
        descent_bands = slices_and(height_bands, descent.get_slices())
        self.create_kpvs_within_slices(air_spd.array, descent_bands, max_value)


class AirspeedTopOfDescentTo10000FtMax(KeyPointValueNode):
    '''
    Outside the USA 10,000 ft relates to flight levels, whereas FAA regulations
    (and possibly others we don't currently know about) relate to height above
    sea level (QNH) hence the options based on landing airport location.

    In either case, we apply some hysteresis to prevent nuisance retriggering
    which can arise if the aircraft is sitting on the 10,000ft boundary.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               alt_qnh=P('Altitude QNH'),
               ldg_apt=A('FDR Landing Airport'),
               descent=S('Descent')):

        country = None
        if ldg_apt.value:
            country = ldg_apt.value.get('location', {}).get('country')

        alt = alt_qnh.array if country == 'United States' else alt_std.array
        alt = hysteresis(alt, HYSTERESIS_FPALT)

        height_bands = np.ma.clump_unmasked(np.ma.masked_less(repair_mask(alt), 10000))
        descent_bands = slices_and(height_bands, descent.get_slices())
        self.create_kpvs_within_slices(air_spd.array, descent_bands, max_value)


class AirspeedTopOfDescentTo4000FtMax(KeyPointValueNode):
    '''
    Outside the USA 4,000 ft relates to flight levels, whereas FAA regulations
    (and possibly others we don't currently know about) relate to height above
    sea level (QNH) hence the options based on landing airport location.

    In either case, we apply some hysteresis to prevent nuisance retriggering
    which can arise if the aircraft is sitting on the 4,000ft boundary.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               alt_qnh=P('Altitude QNH'),
               ldg_apt=A('FDR Landing Airport'),
               descent=S('Descent')):

        country = None
        if ldg_apt.value:
            country = ldg_apt.value.get('location', {}).get('country')

        alt = alt_qnh.array if country == 'United States' else alt_std.array
        alt = hysteresis(alt, HYSTERESIS_FPALT)

        height_bands = np.ma.clump_unmasked(np.ma.masked_less(repair_mask(alt), 4000))
        descent_bands = slices_and(height_bands, descent.get_slices())
        self.create_kpvs_within_slices(air_spd.array, descent_bands, max_value)


class AirspeedTopOfDescentTo4000FtMin(KeyPointValueNode):
    '''
    Outside the USA 4,000 ft relates to flight levels, whereas FAA regulations
    (and possibly others we don't currently know about) relate to height above
    sea level (QNH) hence the options based on landing airport location.

    In either case, we apply some hysteresis to prevent nuisance retriggering
    which can arise if the aircraft is sitting on the 4,000ft boundary.
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               alt_std=P('Altitude STD Smoothed'),
               alt_qnh=P('Altitude QNH'),
               ldg_apt=A('FDR Landing Airport'),
               descent=S('Descent')):

        country = None
        if ldg_apt.value:
            country = ldg_apt.value.get('location', {}).get('country')

        alt = alt_qnh.array if country == 'United States' else alt_std.array
        alt = hysteresis(alt, HYSTERESIS_FPALT)

        height_bands = np.ma.clump_unmasked(np.ma.masked_less(repair_mask(alt), 4000))
        descent_bands = slices_and(height_bands, descent.get_slices())
        self.create_kpvs_within_slices(air_spd.array, descent_bands, min_value)


class AirspeedDuringLevelFlightMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               air_spd=P('Airspeed'),
               lvl_flt=S('Level Flight')):

        for section in lvl_flt:
            self.create_kpv(*max_value(air_spd.array, section.slice))


class ModeControlPanelAirspeedSelectedAt8000FtDescending(KeyPointValueNode):
    '''
    Refactor to be a formatted name node if multiple Airspeed At Altitude
    KPVs are required. Could depend on either Altitude When Climbing or
    Altitude When Descending, but the assumption is that we'll have both.
    '''

    units = 'kt'
    
    def derive(self,
               mcp=P('Mode Control Panel Airspeed Selected'),
               alt_std_desc=S('Altitude When Descending')):
        
        # TODO: Confirm MCP parameter name.
        self.create_kpvs_at_ktis(mcp.array,
                                 alt_std_desc.get(name='8000 Ft Descending'))


##############################################################################
# Alpha Floor


class AlphaFloorDuration(KeyPointValueNode):
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Alpha Floor', 'FMA AT Information'), available)
    
    def derive(self,
               alpha_floor=M('Alpha Floor'),
               autothrottle_info=M('FMA AT Information')):
        combined_alpha_floor = vstack_params_where_state(
            (alpha_floor, 'Engaged'),
            (autothrottle_info, 'Alpha Floor')).any(axis=0)
        self.create_kpvs_from_slice_durations(
            runs_of_ones(combined_alpha_floor), self.frequency)


##############################################################################
# Angle of Attack


class AOAWithFlapMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control. Pitch/Angle of Attack vs stall angles"

    This is an adaptation of the airspeed algorithm, used to determine peak
    AOA vs flap. It may not be possible to obtain stalling angle of attack
    figures to set event thresholds, but a threshold based on in-service data
    may suffice.
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'AOA With Flap %(flap)s Max'
    NAME_VALUES = NAME_VALUES_FLAP
    
    name = 'AOA With Flap Max'
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('AOA', 'Airborne'), available)

    def derive(self, flap_lever=M('Flap Lever'), flap_synth=M('Flap'),
               aoa=P('AOA'), scope=S('Airborne')):
        '''
        '''
        flap = flap_lever or flap_synth
        # Airborne scope avoids triggering during the takeoff or landing runs.
        data = self.flap_or_conf_max_or_min(flap, aoa, max_value,
                                     scope=scope, include_zero=True)
        for index, value, detent in data:
            self.create_kpv(index, value, parameter=flap.name,
                            flap=detent)


class AOADuringGoAroundMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Mis-handled G/A"
    '''

    name = 'AOA During Go Around Max'
    units = 'deg'

    def derive(self,
               aoa=P('AOA'),
               go_arounds=S('Go Around And Climbout')):

        self.create_kpvs_within_slices(aoa.array, go_arounds, max_value)


##############################################################################
class ThrustReversersDeployedDuration(KeyPointValueNode):
    '''
    Measure the duration (secs) which the thrust reverses were deployed for.
    0 seconds represents no deployment at landing.
    '''
    units = 's'

    def derive(self, tr=M('Thrust Reversers'), landings=S('Landing')):
        for landing in landings:
            tr_in_ldg = tr.array[landing.slice]
            dur_deployed = np.ma.sum(tr_in_ldg == 'Deployed') / tr.frequency
            dep_start = find_edges_on_state_change('Deployed', tr_in_ldg)
            if dur_deployed and dep_start:
                index = dep_start[0] + landing.slice.start
            else:
                index = landing.slice.start
            self.create_kpv(index, dur_deployed)


class ThrustReversersCancelToEngStopDuration(KeyPointValueNode):
    '''
    Measure the duration (secs) between the thrust reversers being cancelled and
    the engines being shutdown.
    
    The scope is limited to the engine running period to avoid spurious
    indications of thrust reverser operation while the engine is not running,
    as can happen on some aircraft types.
    '''
    units = 's'

    def derive(self, tr=M('Thrust Reversers'), 
               eng_starts=KTI('Eng Start'),
               eng_stops=KTI('Eng Stop')):
        try:
            start = eng_starts.get_first().index
        except AttributeError:
            start = 0
        try:
            stop = eng_stops.get_last().index
        except AttributeError:
            # If engine did not stop, there is no period between thrust
            # reversers being cancelled and the engine stop
            return
        cancels = find_edges_on_state_change(
            'Deployed',  tr.array[start:stop], change='leaving')
        if cancels:
            # TRs were cancelled before engine stopped
            cancel_index = cancels[-1] + start
            eng_stop_index = eng_stops.get_next(cancel_index).index
            self.create_kpv(eng_stop_index,
                            (eng_stop_index - cancel_index) / self.frequency)


class TouchdownToThrustReversersDeployedDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) Reverse thrust delay - time delay.
    Selection more than 3sec after main wheel t/d."

    Note: 3 second threshold may be applied to derive an event from this KPV.
    '''

    units = 's'

    def derive(self,
               tr=M('Thrust Reversers'),
               landings=S('Landing'),
               touchdowns=KTI('Touchdown')):

        for landing in landings:
            # Only interested in first opening of reversers on this landing:
            deploys = clump_multistate(tr.array, 'Deployed', landing.slice)
            try:
                deployed = deploys[0].start
            except IndexError:
                continue
            touchdown = touchdowns.get_first(within_slice=landing.slice)
            if not touchdown:
                continue
            self.create_kpv(deployed, (deployed - touchdown.index) / tr.hz)


class TouchdownToSpoilersDeployedDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) Late spoiler deployment - time delay".
    '''
    def derive(self, brake=M('Speedbrake Selected'),
               lands = S('Landing'), tdwns=KTI('Touchdown')):
        deploys = find_edges_on_state_change('Deployed/Cmd Up', brake.array, phase=lands)
        for land in lands:
            for deploy in deploys:
                if not is_index_within_slice(deploy, land.slice):
                    continue
                for tdwn in tdwns:
                    if not is_index_within_slice(tdwn.index, land.slice):
                        continue
                    self.create_kpv(deploy, (deploy-tdwn.index)/brake.hz)


class TrackDeviationFromRunway1000To500Ft(KeyPointValueNode):
    '''
    Track deviation from the runway centreline from 1000 to 500 feet.

    Helps establishing the stable criteria for IFR below 1000ft.

    Includes large deviations recoreded when aircraft turns onto runway at
    altitudes below 1000ft.
    '''
    def derive(self, track_dev=P('Track Deviation From Runway'),
               alt=P('Altitude AAL')):
        alt_bands = alt.slices_from_to(1000, 500)
        self.create_kpvs_within_slices(
            track_dev.array,
            alt_bands,
            max_abs_value,
        )

class TrackDeviationFromRunway500To300Ft(KeyPointValueNode):
    '''
    Track deviation from the runway centreline from 500 to 300 feet.

    Helps establishing the stable criteria for VFR below 500ft.

    Includes large deviations recorded when aircraft turns onto runway at
    altitudes below 500ft, but should be stable by 300ft.
    '''
    def derive(self, track_dev=P('Track Deviation From Runway'),
               alt=P('Altitude AAL')):
        alt_bands = alt.slices_from_to(500, 300)
        self.create_kpvs_within_slices(
            track_dev.array,
            alt_bands,
            max_abs_value,
        )


class TrackDeviationFromRunway300FtToTouchdown(KeyPointValueNode):
    '''
    Track deviation from the runway centreline from 300 to 0 feet.

    Helps establishing the FAA stable criteria for a late roll onto runway
    heading.

    There is almost no excuse for being unaligned with the runway at this
    altitude, so the distribution should have small variance.
    '''
    def derive(self, track_dev=P('Track Deviation From Runway'),
               alt=P('Altitude AAL')):
        alt_bands = alt.slices_from_to(300, 0)
        self.create_kpvs_within_slices(
            track_dev.array,
            alt_bands,
            max_abs_value,
        )


##############################################################################
# TOGA Usage


class TOGASelectedDuringFlightDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control - Unexpected TOGA power selection in flight (except for
    a go-around)"

    Note: This covers the entire airborne phase excluding go-arounds.
    '''

    name = 'TOGA Selected During Flight Not Go Around Duration'
    units = 's'

    def derive(self,
               toga=M('Takeoff And Go Around'),
               go_arounds=S('Go Around And Climbout'),
               airborne=S('Airborne')):

        to_scan = slices_and(
            [s.slice for s in airborne],
            slices_not(
                [s.slice for s in go_arounds],
                begin_at=airborne[0].slice.start,
                end_at=airborne[-1].slice.stop,
            ),
        )
        self.create_kpvs_where(toga.array == 'TOGA', toga.hz,
                               phase=to_scan, exclude_leading_edge=True)


class TOGASelectedDuringGoAroundDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control - TOGA power selection in flight (Go-arounds need to be
    kept as a separate case)."
    '''

    name = 'TOGA Selected During Go Around Duration'
    units = 's'

    def derive(self, toga=M('Takeoff And Go Around'),
               go_arounds=S('Go Around And Climbout')):
        self.create_kpvs_where(toga.array == 'TOGA',
                               toga.hz, phase=go_arounds)


##############################################################################


class LiftoffToClimbPitchDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Slow climb out after rotation and
    slow rotation."

    This KPV originally used a threshold of 12.5 deg nose up, as suggested by
    the CAA, however it was found that some corporate operators do not
    achieve this attitude, so a lower threshold of 10deg was adopted.

    An endpoint of a minute after liftoff was added to avoid triggering well
    after the period of interest, and a pre-liftoff extension included for
    cases which rotate quickly and reach 10deg before liftoff !
    '''

    def derive(self, pitch=P('Pitch'),lifts=KTI('Liftoff')):
        for lift in lifts:
            pitch_up_idx = index_at_value(pitch.array, 10.0,
                                          _slice=slice(lift.index-5*pitch.hz,
                                                       lift.index+60.0*pitch.hz))
            if pitch_up_idx:
                duration = (pitch_up_idx - lift.index)/pitch.hz
                self.create_kpv(pitch_up_idx, duration)


##############################################################################
# Landing Gear


##################################
# Braking


class BrakePressureInTakeoffRollMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take off (Lateral)". Primary Brake pressure during ground
    roll. Could also be applicable to longitudinal excursions on take-off.
    This is to capture scenarios where the brake is accidentally used when
    using the rudder (dragging toes on pedals)."
    '''
    def derive(self, bp=P('Brake Pressure'), rolls=S('Takeoff Roll')):
        self.create_kpvs_within_slices(bp.array, rolls, max_value)


# XXX: Can minus_60 fall outside end of landing slice? Fix if needed.
class DelayedBrakingAfterTouchdown(KeyPointValueNode):
    '''
    This parameter was requested by one customer, who asked us to adopt the
    Airbus AFPS implementation.
    '''
    def derive(self,
               lands=S('Landing'),
               gs=P('Groundspeed'),
               tdwns=KTI('Touchdown')):
        '''
        '''
        for land in lands:
            for tdwn in tdwns:
                if not is_index_within_slice(tdwn.index, land.slice):
                    continue
                gs_td = value_at_index(gs.array, tdwn.index)
                if gs_td is None:
                    continue
                minus_10 = index_at_value(gs.array, gs_td - 10.0, land.slice)
                minus_60 = index_at_value(gs.array, gs_td - 60.0, land.slice)
                if minus_10 is None or minus_60 is None:
                    continue
                dt = (minus_60 - minus_10) / gs.frequency
                self.create_kpv((minus_10 + minus_60) / 2.0, dt)


class AutobrakeRejectedTakeoffNotSetDuringTakeoff(KeyPointValueNode):
    '''
    '''
    units = 's'

    def derive(self,
               ab_rto=M('Autobrake Selected RTO'),
               takeoff=S('Takeoff Roll')):

        # In order to avoid false positives, so we assume masked values are
        # Selected.
        not_selected = (ab_rto.array != 'Selected').filled(False)
        self.create_kpvs_where(
            not_selected,
            ab_rto.hz, phase=takeoff)


##############################################################################
# Altitude


########################################
# Altitude: General


class AltitudeMax(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_std=P('Altitude STD Smoothed'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(alt_std.array, airborne, max_value)


class AltitudeSTDAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'ft'
    name = 'Altitude STD At Liftoff'

    def derive(self,
               alt_std=P('Altitude STD Smoothed'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(alt_std.array, liftoffs)


class AltitudeQNHAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'ft'
    name = 'Altitude QNH At Liftoff'

    def derive(self,
               alt_std=P('Altitude QNH'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(alt_std.array, liftoffs)


class AltitudeSTDAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'ft'
    name = 'Altitude STD At Touchdown'

    def derive(self,
               alt_std=P('Altitude STD Smoothed'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(alt_std.array, touchdowns)


class AltitudeQNHAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'ft'
    name = 'Altitude QNH At Touchdown'

    def derive(self,
               alt_std=P('Altitude QNH'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(alt_std.array, touchdowns)


class AltitudeDuringGoAroundMin(KeyPointValueNode):
    '''
    The altitude above the local airfield level at the minimum altitude point
    of the go-around.

    Note: This may be less than the radio altimeter reading at this point if
    there is higher ground in the area of the go-around minimum point.
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               go_arounds=KTI('Go Around')):

        self.create_kpvs_at_ktis(alt_aal.array, go_arounds)


class AltitudeOvershootAtSuspectedLevelBust(KeyPointValueNode):
    '''
    FDS refined this KPV as part of the UK CAA Significant Seven programme.

    "Airborne Conflict (Mid-Air Collision) Level Busts (>300ft from an
    assigned level) It would be useful if this included overshoots of cleared
    level, i.e. a reversal of more than 300ft".
    
    Q: Could we compare against Altitude Selected to know if the aircraft should
       be climbing or descending?
    '''

    units = 'ft'

    def derive(self,
               alt_std=P('Altitude STD Smoothed'),
               alt_aal=P('Altitude AAL')):

        bust = 300  # ft
        bust_time = 3 * 60  # 3 mins
        bust_length = bust_time * self.frequency

        idxs, peaks = cycle_finder(alt_std.array, min_step=bust)

        if idxs is None:
            return
        for num, idx in enumerate(idxs[1: -1]):
            begin = index_at_value(np.ma.abs(alt_std.array - peaks[num + 1]),
                                   bust, _slice=slice(idx, None, -1))
            end = index_at_value(np.ma.abs(alt_std.array - peaks[num + 1]), bust,
                                 _slice=slice(idx, None))
            if begin and end:
                duration = (end - begin) / alt_std.frequency
                if duration < bust_time:
                    a = alt_std.array[idxs[num]]  # One before the peak of interest
                    b = alt_std.array[idxs[num + 1]]  # The peak of interest
                    # The next one (index reduced to avoid running beyond end of
                    # data)
                    c = alt_std.array[idxs[num + 2] - 1]
                    idx_from = max(0, idxs[num + 1] - bust_length)
                    idx_to = min(len(alt_std.array), idxs[num + 1] + bust_length)
                    if b > (a + c) / 2:
                        overshoot = min(b - a, b - c)
                        #if overshoot > 5000:
                        if overshoot > alt_aal.array[idxs[num + 1]] / 4:
                            # This happens normally on short sectors or training flights.
                            continue
                        # Include a scan over the preceding and following
                        # bust_time in case the preceding or following peaks
                        # were outside this timespan.
                        alt_a = np.ma.min(alt_std.array[idx_from:idxs[num + 1]])
                        alt_c = np.ma.min(alt_std.array[idxs[num + 1]:idx_to])
                        overshoot = min(b - a, b - alt_a, b - alt_c, b - c)
                        self.create_kpv(idx, overshoot)
                    else:
                        undershoot = max(b - a, b - c)
                        if undershoot < -alt_aal.array[idxs[num + 1]]/4:
                            continue
                        alt_a = np.ma.max(alt_std.array[idx_from:idxs[num + 1]])
                        alt_c = np.ma.max(alt_std.array[idxs[num + 1]:idx_to])
                        undershoot = max(b - a, b - alt_a, b - alt_c, b - c)
                        self.create_kpv(idx, undershoot)


class CabinAltitudeWarningDuration(KeyPointValueNode):
    '''
    The duration of the Cabin Altitude Warning signal.
    '''
    units = 's'

    def derive(self,
               cab_warn=M('Cabin Altitude Warning'),
               airborne=S('Airborne')):

        self.create_kpvs_where(cab_warn.array == 'Warning',
                               cab_warn.hz, phase=airborne)


class AltitudeDuringCabinAltitudeWarningMax(KeyPointValueNode):
    '''
    The maximum aircraft altitude when the Cabin Altitude Warning was sounding.
    '''
    units = 'ft'

    def derive(self,
               cab_warn=M('Cabin Altitude Warning'),
               airborne=S('Airborne'),
               alt=P('Altitude STD')):
        # Grr... no test case and use of incorrect state
        #todo: warns = runs_of_ones(cab_warn.array == 'Warning')
        warns = np.ma.clump_unmasked(np.ma.masked_equal(cab_warn.array,0))
        air_warns = slices_and(warns, airborne.get_slices())
        self.create_kpvs_within_slices(alt.array, air_warns, max_value)


class CabinAltitudeMax(KeyPointValueNode):
    '''
    The maximum Cabin Altitude - applies on every flight.
    '''
    units = 'ft'

    def derive(self,
               cab_alt=P('Cabin Altitude'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(cab_alt.array, 
                                       airborne,
                                       max_value)



########################################
# Altitude: Flap


class AltitudeWithFlapMax(KeyPointValueNode):
    '''
    The exceedance being detected here is the altitude reached with flaps not
    stowed, hence any flap value greater than zero is applicable and we're not
    really interested (for the purpose of identifying the event) what flap
    setting was reached.
    '''

    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Altitude STD Smoothed', 'Airborne'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               alt_std=P('Altitude STD Smoothed'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        alt_flap = alt_std.array * np.ma.minimum(flap.array, 1.0)
        self.create_kpvs_within_slices(alt_flap, airborne, max_value)


class AltitudeAtFlapExtension(KeyPointValueNode):
    '''
    Records the altitude at every flap extension in flight.
    '''

    units = 'ft'

    def derive(self,
               flaps=KTI('Flap Extension While Airborne'),
               alt_aal=P('Altitude AAL')):

        if flaps:
            for flap in flaps:
                value = value_at_index(alt_aal.array, flap.index)
                self.create_kpv(flap.index, value)


class AltitudeAtVNAVModeAndEngThrustModeRequired(KeyPointValueNode):
    '''
    '''
    
    name = 'Altitude At VNAV Mode And Eng Thrust Mode Required'
    
    units = 'ft'
    
    def derive(self,
               alt_aal=P('Altitude AAL'),
               vnav_thrust=KTI('VNAV Mode And Eng Thrust Mode Required')):
        
        self.create_kpvs_at_ktis(alt_aal.array, vnav_thrust)


class AltitudeAtFirstFlapExtensionAfterLiftoff(KeyPointValueNode):
    '''
    Separates the first flap extension.
    '''

    units = 'ft'

    def derive(self, flap_exts=KPV('Altitude At Flap Extension')):
        # First Flap Extension within Airborne section should be first after
        # liftoff.
        flap_ext = flap_exts.get_first()
        if flap_ext:
            self.create_kpv(flap_ext.index, flap_ext.value)


class AltitudeAtFlapExtensionWithGearDown(KeyPointValueNode):
    '''
    Prepared to cover one customer's SOP relating to selection of Flap 20 on
    the approach.
    '''

    NAME_FORMAT = 'Altitude At Flap %(flap)s Extension With Gear Down'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return (('Flap Lever' in available or 'Flap' in available) and
                all_of(('Altitude AAL', 'Gear Extended', 'Airborne'),
                       available))
    
    def derive(self, flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               alt_aal=P('Altitude AAL'),
               gear_ext=S('Gear Extended'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        
        for air_down in slices_and([a.slice for a in airborne], 
                                   [g.slice for g in gear_ext]):
            extend_indexes = np.ma.where(np.ma.diff(flap.array.raw[air_down])>0)[0]
            if len(extend_indexes)==0:
                continue
            for extend_index in extend_indexes:
                # The flap we are moving to is +1 from the diff index
                index = (air_down.start or 0) + extend_index + 1
                value = alt_aal.array[index]
                selected_flap = int(flap.array.raw[index])
                self.create_kpv(index, value, flap=int(selected_flap))


class AirspeedAtFlapExtensionWithGearDown(KeyPointValueNode):
    '''
    Prepared to cover one customer's SOP relating to selection of Flap 20 on
    the approach.
    '''

    NAME_FORMAT = 'Airspeed At Flap %(flap)s Extension With Gear Down'
    NAME_VALUES = NAME_VALUES_FLAP
    
    units = 'kts'
    
    @classmethod
    def can_operate(cls, available):
        return (all_of(('Airspeed', 'Gear Extended', 'Airborne'), available) and
                ('Flap Lever' in available or 'Flap' in available))
    
    def derive(self, flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               air_spd=P('Airspeed'),
               gear_ext=S('Gear Extended'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        
        for air_down in slices_and([a.slice for a in airborne], 
                                   [g.slice for g in gear_ext]):
            extend_indexes = np.ma.where(np.ma.diff(flap.array.raw[air_down])>0)[0]
            if len(extend_indexes)==0:
                continue
            for extend_index in extend_indexes:
                # The flap we are moving to is +1 from the diff index
                index = (air_down.start or 0) + extend_index + 1
                value = air_spd.array[index]
                selected_flap = int(flap.array.raw[index])
                self.create_kpv(index, value, flap=int(selected_flap))


class AltitudeRadioCleanConfigurationMin(KeyPointValueNode):
    def derive(self,
               alt_rad=P('Altitude Radio'),
               flap=M('Flap'),
               gear_retr=S('Gear Retracted')):

        alt_rad_noflap = np.ma.masked_where(flap.array != 0.0, alt_rad.array)
        self.create_kpvs_within_slices(alt_rad_noflap, gear_retr, min_value)



##########################################################################
# Speeds relative to Vref Flap 30 + 80 kts (Boeing procedures for 757/767).
##########################################################################

class AirspeedRelativeAtFirstFlapRetraction(KeyPointValueNode):
    '''
    Specific to certain 757/767 operations, this is the speed relative to the
    Vref for flap 30 plus 80kts, at the point of first flap retraction.
    '''
    
    name = 'Airspeed Relative To Vref30+80 At First Flap Retraction'

    units = 'kts'

    def derive(self, airspeed=P('Airspeed'),
               gw=P('Gross Weight Smoothed'),
               alt_retract=KPV('Altitude At First Flap Change After Liftoff'),
               series=A('Series'),
               engine=A('Engine Type')):

        if series.value not in ['B757-200(F)', 'B767-300F(ER)']:
            return
        if alt_retract:
            index = alt_retract[0].index
            speed_ret = value_at_index(airspeed.array, index)
            gw_ret = value_at_index(gw.array, index)
            # Get the Vref speed for flap 30 and the current weight.
            vref = vspeed_lookup('Vref', series.value, engine.value, 30.0, gw_ret)
            self.create_kpv(index, speed_ret-(vref+80.0))


class AirspeedRelativeAtFirstFlapExtensionWithGearDown(KeyPointValueNode):
    '''
    Specific to certain 757/767 operations, this is the speed relative to the
    Vref at the landing weight, assuming a flap 30 landing, plus 80kts.
    '''
    
    name = 'Airspeed Relative To Vref30+80 At First Flap Extension With Gear Down'

    units = 'kts'

    def derive (self, speed=KPV('Airspeed At Flap Extension With Gear Down'),
                gw_ldg=KPV('Gross Weight At Touchdown'),
                series=A('Series'),
                engine=A('Engine Type')):

        if series.value not in ['B757-200(F)', 'B767-300F(ER)']:
            return

        kpv_20 = [k for k in speed if '20' in k.name]
        if kpv_20==[]:
            # The crew did not select flap 20 with gear down on this flight.
            return
        # If there were multiple flap deployments, we'll just use the last one.
        index = kpv_20[-1].index
        speed_20 = kpv_20[-1].value
        
        # Get the Vref speed for a landing at flap 30 and the projected weight.
        vref_ldg = vspeed_lookup('Vref', series.value, engine.value, 30.0, gw_ldg[0].value)
        self.create_kpv(index, speed_20-(vref_ldg+80.0))

class AirspeedRelativeAtFlap20SelectionWithGearDown(KeyPointValueNode):
    '''
    Specific to certain 757/767 operations, this is the speed relative to the
    Vref at the landing weight, assuming a flap 30 landing, plus 80kts.
    '''

    name = 'Airspeed Relative To Vref30+80 At Flap 20 Selection With Gear Down'
    units = 'kts'

    def derive (self, speed=KPV('Airspeed At Flap Extension With Gear Down'),
                gw_ldg=KPV('Gross Weight At Touchdown'),
                series=A('Series'), engine=A('Engine Type'),):

        if series.value not in ['B757-200(F)', 'B767-300F(ER)']:
            return

        kpv_20 = speed.get_ordered_by_index()
        if kpv_20==[]:
            # The crew did not extend flap with gear down on this flight.
            return
        # We are only interested in the first one.
        index = kpv_20[0].index
        speed_20 = kpv_20[0].value
        
        # Get the Vref speed for a landing at flap 30 and the projected weight.
        vref_ldg = vspeed_lookup('Vref', series.value, engine.value, 30.0, gw_ldg[0].value)
        self.create_kpv(index, speed_20-(vref_ldg+80.0))

##########################################################################


class AltitudeAtFirstFlapChangeAfterLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return (('Flap Lever' in available or 'Flap' in available) and
                all_of(('Flap At Liftoff', 'Altitude AAL', 'Airborne'),
                       available))

    def derive(self, flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               flap_liftoff=KPV('Flap At Liftoff'),
               alt_aal=P('Altitude AAL'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        
        if flap_liftoff == []:
            # No Flap at Liftoff identified
            return

        if flap_liftoff.get_first().value == 0.0:
            # if taken off with flap 0 likely first flap change after liftoff
            # will be on approach to land which we are not interested in
            # here.
            return

        for air in airborne:
            change_indexes = np.ma.where(np.ma.diff(flap.array[air.slice]))[0]
            if len(change_indexes):
                # Create at first change:
                index = (air.slice.start or 0) + change_indexes[0] +0.5
                self.create_kpv(index, value_at_index(alt_aal.array, index))


class AltitudeAtLastFlapChangeBeforeTouchdown(KeyPointValueNode):
    '''
    '''
    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return (('Flap Lever' in available or 'Flap' in available) and
                'Altitude AAL' in available and 'Touchdown' in available)

    def derive(self, flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               alt_aal=P('Altitude AAL'),
               touchdowns=KTI('Touchdown')):
        flap = flap_lever or flap_synth

        for touchdown in touchdowns:
            land_flap = flap.array.raw[touchdown.index]
            flap_move = abs(flap.array.raw-land_flap)
            rough_index = index_at_value(flap_move, 0.5, slice(touchdown.index, 0, -1))
            # index_at_value tries to be precise, but in this case we really
            # just want the index at the new flap setting.
            if rough_index:
                last_index = np.round(rough_index)
                alt_last = value_at_index(alt_aal.array, last_index)
                self.create_kpv(last_index, alt_last)


class AltitudeAtFirstFlapRetractionDuringGoAround(KeyPointValueNode):
    '''
    Go Around Flap Retracted pinpoints the flap retraction instance within the
    500ft go-around window. Create a single KPV for the first flap retraction
    within a Go Around And Climbout phase.
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               flap_rets=KTI('Flap Retraction During Go Around'),
               go_arounds=S('Go Around And Climbout')):

        for go_around in go_arounds:
            flap_ret = flap_rets.get_first(within_slice=go_around.slice)
            if flap_ret:
                self.create_kpv(flap_ret.index, alt_aal.array[flap_ret.index])


class AltitudeAtFirstFlapRetraction(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               flap_rets=KTI('Flap Retraction While Airborne')):
        
        flap_ret = flap_rets.get_first()
        if flap_ret:
            self.create_kpv(flap_ret.index, alt_aal.array[flap_ret.index])


class AltitudeAtLastFlapRetraction(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               flap_rets=KTI('Flap Retraction While Airborne')):
        
        flap_ret = flap_rets.get_last()
        if flap_ret:
            self.create_kpv(flap_ret.index, alt_aal.array[flap_ret.index])


class AltitudeAtClimbThrustDerateDeselectedDuringClimbBelow33000Ft(KeyPointValueNode):
    '''
    Specific to 787 operations.
    '''
    
    units = 'ft'
    
    def derive(self, alt_aal=P('Altitude AAL'),
               derate_deselecteds=KTI('Climb Thrust Derate Deselected'),
               climbs=S('Climbing')):
        for derate_deselected in derate_deselecteds.get(within_slices=climbs.get_slices()):
            alt_aal_value = value_at_index(alt_aal.array,
                                           derate_deselected.index)
            if alt_aal_value < 33000:
                self.create_kpv(derate_deselected.index, alt_aal_value)


########################################
# Altitude: Gear


class AltitudeAtGearDownSelection(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               gear_dn_sel=KTI('Gear Down Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, gear_dn_sel)


class AltitudeAtGearDownSelectionWithFlapDown(KeyPointValueNode):
    '''
    Inclusion of the "...WithFlap" term is intended to exclude data points
    where only the gear is down (these are exceptional occasions where gear
    has been extended with flaps up to burn extra fuel).
    '''

    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Altitude AAL', 'Gear Down Selection'), available)

    def derive(self,
               alt_aal=P('Altitude AAL'),
               gear_downs=KTI('Gear Down Selection'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap')):
        flap = flap_lever or flap_synth
        flap_dns = np.ma.clump_unmasked(np.ma.masked_equal(flap.array, 0.0))
        flap_dn_gear_downs = gear_downs.get(within_slices=flap_dns)
        self.create_kpvs_at_ktis(alt_aal.array, flap_dn_gear_downs)


class AltitudeAtGearUpSelection(KeyPointValueNode):
    '''
    Gear up selections after takeoff, not following a go-around (when it is
    normal to retract gear at significant height).
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               gear_up_sel=KTI('Gear Up Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, gear_up_sel)


class AltitudeAtGearUpSelectionDuringGoAround(KeyPointValueNode):
    '''
    Finds the relative altitude at which gear up was selected from the point of
    minimum altitude in the go-around. If gear up was selected before that,
    just set the value to zero.
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               go_arounds=S('Go Around And Climbout'),
               gear_up_sel=KTI('Gear Up Selection During Go Around')):

        for go_around in go_arounds:
            # Find the index and height at this go-around minimum:
            pit_index, pit_value = min_value(alt_aal.array, go_around.slice)
            for gear_up in gear_up_sel.get(within_slice=go_around.slice):
                if gear_up.index > pit_index:
                    # Use height between go around minimum and gear up:
                    gear_up_ht = alt_aal.array[gear_up.index] - pit_value
                else:
                    # Use zero if gear up selected before minimum height:
                    gear_up_ht = 0.0
                self.create_kpv(gear_up.index, gear_up_ht)


class AltitudeWithGearDownMax(KeyPointValueNode):
    '''
    Maximum height above the airfield with the gear down.
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               gear=M('Gear Down'),
               airs=S('Airborne')):

        gear.array[gear.array != 'Down'] = np.ma.masked
        gear_downs = np.ma.clump_unmasked(gear.array)
        self.create_kpvs_within_slices(
            alt_aal.array, slices_and(airs.get_slices(), gear_downs),
            max_value)


class AltitudeSTDWithGearDownMax(KeyPointValueNode):
    '''
    In extreme cases, it's the pressure altitude we are interested in, not
    just the altitude above the airfield (already covered by
    "Altitude With Gear Down Max")
    '''

    name = 'Altitude STD With Gear Down Max'
    units = 'ft'

    def derive(self,
               alt_std=P('Altitude STD'),
               gear=M('Gear Down'),
               airs=S('Airborne')):

        gear.array[gear.array != 'Down'] = np.ma.masked
        gear_downs = np.ma.clump_unmasked(gear.array)
        self.create_kpvs_within_slices(
            alt_std.array, slices_and(airs.get_slices(), gear_downs),
            max_value)


class AltitudeAtGearDownSelectionWithFlapUp(KeyPointValueNode):
    
    units = 'ft'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Altitude AAL', 'Gear Down Selection'), available)
    
    def derive(self,
               alt_aal=P('Altitude AAL'),
               gear_downs=KTI('Gear Down Selection'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap')):
        flap = flap_lever or flap_synth
        flap_ups = np.ma.clump_unmasked(np.ma.masked_greater(flap.array, 0))
        flap_up_gear_downs = gear_downs.get(within_slices=flap_ups)
        self.create_kpvs_at_ktis(alt_aal.array, flap_up_gear_downs)


########################################
# Altitude: Automated Systems


class AltitudeAtAPEngagedSelection(KeyPointValueNode):
    '''
    '''

    name = 'Altitude At AP Engaged Selection'
    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               ap_eng=KTI('AP Engaged Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, ap_eng)


class AltitudeAtAPDisengagedSelection(KeyPointValueNode):
    '''
    '''

    name = 'Altitude At AP Disengaged Selection'
    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               ap_dis=KTI('AP Disengaged Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, ap_dis)


class AltitudeAtATEngagedSelection(KeyPointValueNode):
    '''
    Note: Autothrottle is normally engaged prior to takeoff, so will not
          trigger this event.
    '''

    name = 'Altitude At AT Engaged Selection'
    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               at_eng=KTI('AT Engaged Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, at_eng)


class AltitudeAtATDisengagedSelection(KeyPointValueNode):
    '''
    '''

    name = 'Altitude At AT Disengaged Selection'
    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               at_dis=KTI('AT Disengaged Selection')):

        self.create_kpvs_at_ktis(alt_aal.array, at_dis)


class AltitudeAtFirstAPEngagedAfterLiftoff(KeyPointValueNode):
    '''
    '''

    name = 'Altitude At First AP Engaged After Liftoff'
    units = 'ft'

    def derive(self,
               ap=KTI('AP Engaged'),
               alt_aal=P('Altitude AAL'),
               airborne=S('Airborne')):


        change_indexes = find_edges_on_state_change('Engaged', ap.array,
                                                    phase=airborne)
        if len(change_indexes):
            # Create at first change:
            index = change_indexes[0]
            self.create_kpv(index, value_at_index(alt_aal.array, index))

########################################
# Altitude: Mach


class AltitudeAtMachMax(KeyPointValueNode):
    '''
    '''
    units = 'ft'

    def derive(self,
               alt_std=P('Altitude STD Smoothed'),
               max_mach=KPV('Mach Max')):
        # Aligns altitude to mach to ensure we have the most accurate altitude
        # reading at the point of maximum mach:
        self.create_kpvs_at_kpvs(alt_std.array, max_mach)


########################################
# Stable Approach analysis

class AltitudeFirstStableDuringLastApproach(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish first point stable during the last approach i.e. a full stop
    landing
    
    Should the approach have not become stable, the altitude will read 0 ft,
    indicating that it was unstable all the way to touchdown.
    '''
    def derive(self, stable=P('Stable Approach'), alt=P('Altitude AAL')):
        # no need for approaches as we can assume each approach has no masked
        # values and inbetween there will be some
        apps = np.ma.clump_unmasked(stable.array)
        if apps:
            # we're only interested in the last approach - we assume that
            # this was the one which came to a full stop
            app = apps[-1]
            index = index_of_first_start(stable.array == 'Stable', app, min_dur=2)
            if index:
                self.create_kpv(index, value_at_index(alt.array, index))
            else:
                # force an altitude of 0 feet at the end of the approach
                self.create_kpv(app.stop-0.5, 0)


class AltitudeFirstStableDuringApproachBeforeGoAround(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish first point stable during all but the last approach. Here we
    assume that these approaches were followed by a Go Around (or possible a
    Touch and Go).
    
    Should the approach have not become stable, the altitude will read 0 ft,
    indicating that it was constantly unstable.
    '''
    def derive(self, stable=P('Stable Approach'), alt=P('Altitude AAL')):
        # no need for approaches as we can assume each approach has no masked
        # values and inbetween there will be some
        apps = np.ma.clump_unmasked(stable.array)
        for app in apps[:-1]:
            # iterate through approaches as only one KPV is to be created per
            # approach
            index = index_of_first_start(stable.array == 'Stable', app, min_dur=2)
            if index:
                self.create_kpv(index, value_at_index(alt.array, index))
            else:
                self.create_kpv(app.stop-0.5, 0)
                

class AltitudeLastUnstableDuringLastApproach(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish last Unstable altitude during the last approach i.e. a full stop
    landing.
    
    Should the approach have not become stable, the altitude will read 0 ft,
    indicating that it was unstable all the way to touchdown.
    '''
    def derive(self, stable=P('Stable Approach'), alt=P('Altitude AAL')):
        apps = np.ma.clump_unmasked(stable.array)
        if apps:
            # we're only interested in the last approach - we assume that
            # this was the one which came to a full stop
            app = apps[-1]
            index = index_of_last_stop(stable.array != 'Stable', app, min_dur=2)
            # Note: Assumed will never have an approach which is 100% Stable
            self.create_kpv(index, value_at_index(alt.array, index))


class AltitudeLastUnstableDuringApproachBeforeGoAround(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish last Unstable altitude during all but the last approach. Here we
    assume that these approaches were followed by a Go Around (or possible a
    Touch and Go).
    
    Should the approach have not become stable, the altitude will read 0 ft,
    indicating that it was constantly unstable.
    '''
    def derive(self, stable=P('Stable Approach'), alt=P('Altitude AAL')):
        apps = np.ma.clump_unmasked(stable.array)
        for app in apps[:-1]:
            index = index_of_last_stop(stable.array != 'Stable', app, min_dur=2)
            if index > app.stop -1:
                # approach ended unstable
                # we were not stable so force altitude of 0 ft
                self.create_kpv(app.stop-0.5, 0)
            else:
                self.create_kpv(index, value_at_index(alt.array, index))


class LastUnstableStateDuringLastApproach(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish last Unstable state (integer representation of the "Stable"
    parameter's values_mapping) during each approach which was followed by a 
    Go Around (or possibly a Touch and Go).
    
    Particuarly of interest to know the reason for instability should the
    Last Unstable condition be at a low altitude.
    '''
    def derive(self, stable=P('Stable Approach')):
        apps = np.ma.clump_unmasked(stable.array)
        if apps:
            # we're only interested in the last approach - we assume that
            # this was the one which came to a full stop
            app = apps[-1]
            index = index_of_last_stop(stable.array != 'Stable', app, min_dur=2)
            # Note: Assumed will never have an approach which is 100% Stable
            self.create_kpv(index, stable.array.raw[index])


class LastUnstableStateDuringApproachBeforeGoAround(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Establish last Unstable state (integer representation of the "Stable"
    parameter's values_mapping) during each approach which was followed by a 
    Go Around (or possibly a Touch and Go).
    
    Can help to determine the reason for choosing not to land.
    '''
    def derive(self, stable=P('Stable Approach')):
        apps = np.ma.clump_unmasked(stable.array)
        for app in apps[:-1]:
            index = index_of_last_stop(stable.array != 'Stable', app, min_dur=2)
            # Note: Assumed will never have an approach which is 100% Stable
            self.create_kpv(index, stable.array.raw[index])


class PercentApproachStable(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.

    Creates a KPV at 1000 ft and 500 ft during the approach with the percent
    (0% to 100%) of the approach that was stable. 
    
    Creates separate names for approaches before a Go Around (or possibly a
    Touch and Go) and those for the Last Landing (assuming a full stop
    landing)
    '''
    NAME_FORMAT = "Percent Approach Stable Below %(altitude)d Ft %(approach)s"
    NAME_VALUES = {
        'altitude' : (1000, 500),
        'approach' : ('During Last Approach', 'During Approach Before Go Around'),
        }
    
    def derive(self, stable=P('Stable Approach'), alt=P('Altitude AAL')):
        apps = np.ma.clump_unmasked(stable.array)
        for n, app in enumerate(apps):
            if n < len(apps)-1:
                approach_type = 'During Approach Before Go Around'
            else:
                approach_type = 'During Last Approach'
                
            stable_app = stable.array[app]
            alt_app = alt.array[app]
            # ensure that stability on ground does not contribute to percentage
            stable_app[alt_app <= 0] = np.ma.masked
            
            for level in (1000, 500):
                # mask out data above the altitude level
                stable_app[alt_app > level] = np.ma.masked
                is_stable = stable_app == 'Stable'
                percent = np.ma.sum(is_stable) / float(np.ma.count(is_stable)) * 100
                # find first stable point (if not, argmax returns 0)
                index = np.ma.argmax(is_stable) + app.start
                self.create_kpv(index, percent, 
                                altitude=level, approach=approach_type)


class AltitudeAtLastAPDisengagedDuringApproach(KeyPointValueNode):
    '''
    This monitors the altitude at which autopilot was last disengaged during
    the cruise.
    '''
    name = 'Altitude At Last AP Disengaged During Approach'
    units = 'ft'

    def derive(self, alt_aal=P('Altitude AAL'), 
               ap_dis=KTI('AP Disengaged Selection'),
               apps=App('Approach Information')):
        ktis = []
        for app in apps:
            ap_dis_kti = ap_dis.get_last(within_slice=app.slice)
            if ap_dis_kti:
                ktis.append(ap_dis_kti)
        self.create_kpvs_at_ktis(alt_aal.array, ktis)


##############################################################################
# Autopilot


class APDisengagedDuringCruiseDuration(KeyPointValueNode):
    '''
    This monitors the duration for which all autopilot channels are disengaged
    in the cruise.
    '''
    name = 'AP Disengaged During Cruise Duration'
    units = 's'

    def derive(self, ap=M('AP Engaged'), cruise=S('Cruise')):
        self.create_kpvs_where(ap.array != 'Engaged', ap.hz, phase=cruise)


##############################################################################
# Configuration


class ConfigurationAtTouchdown(KeyPointValueNode):

    def derive(self,
               configuration=P('Configuration'),
               touchdowns=S('Touchdown')):
        self.create_kpvs_at_ktis(configuration.array, touchdowns)


##############################################################################


class ControlColumnStiffness(KeyPointValueNode):
    """
    The control force and displacement of the flying controls should follow a
    predictable relationship. This parameter is included to identify
    stiffness in the controls in flight.
    """
    def derive(self,
               force=P('Control Column Force'),
               disp=P('Control Column'),
               fast=S('Fast')):
        # We only test during high speed operation to avoid "testing" the
        # full and free movements before flight.
        for speedy in fast:
            # We look for forces above a threshold to detect manual input.
            # This is better than looking for movement, as in the case of
            # stiff controls there is more force but less movement, hence
            # using a movement threshold will tend to be suppressed in the
            # cases we are looking to detect.
            push = force.array[speedy.slice]
            column = disp.array[speedy.slice]

            moves = np.ma.clump_unmasked(
                np.ma.masked_less(np.ma.abs(push),
                                  CONTROL_FORCE_THRESHOLD))
            for move in moves:
                if slice_samples(move) < 10:
                    continue
                corr, slope, off = \
                    coreg(push[move], indep_var=column[move], force_zero=True)
                if corr>0.85:  # This checks the data looks sound.
                    when = np.ma.argmax(np.ma.abs(push[move]))
                    self.create_kpv(
                        (speedy.slice.start or 0) + move.start + when, slope)


class ControlColumnForceMax(KeyPointValueNode):
    units = 'lbf'

    def derive(self,
               force=P('Control Column Force'),
               fast=S('Fast')):
        self.create_kpvs_within_slices(
            force.array, fast.get_slices(),
            max_value)


class ControlWheelForceMax(KeyPointValueNode):
    units = 'lbf'

    def derive(self,
               force=P('Control Wheel Force'),
               fast=S('Fast')):
        self.create_kpvs_within_slices(
            force.array, fast.get_slices(),
            max_value)


##############################################################################
# Runway Distances at Takeoff

class DistanceFromLiftoffToRunwayEnd(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Runway remaining at rotation"
    '''

    units = 'm'

    def derive(self, lat_lift=KPV('Latitude Smoothed At Liftoff'),
               lon_lift=KPV('Longitude Smoothed At Liftoff'),
               rwy=A('FDR Takeoff Runway')):
        if ambiguous_runway(rwy) or not lat_lift:
            return
        toff_end = runway_distance_from_end(rwy.value,
                                            lat_lift[0].value,
                                            lon_lift[0].value)
        self.create_kpv(lat_lift[0].index, toff_end)


class DistanceFromRotationToRunwayEnd(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Runway remaining at rotation"
    '''

    def derive(self, lat=P('Latitude Smoothed'),
               lon=P('Longitude Smoothed'),
               rwy=A('FDR Takeoff Runway'),
               toff_rolls=S('Takeoff Roll')):

        if ambiguous_runway(rwy):
            return
        for roll in toff_rolls:
            rot_idx = roll.stop_edge
            rot_end = runway_distance_from_end(rwy.value,
                                                lat.array[rot_idx],
                                                lon.array[rot_idx])
            self.create_kpv(rot_idx, rot_end)

class DecelerationToAbortTakeoffAtRotation(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Runway remaining at rotation"
    '''

    units = 'g'

    def derive(self, lat=P('Latitude Smoothed'),
               lon=P('Longitude Smoothed'),
               gspd=P('Groundspeed'),
               aspd=P('Airspeed True'),
               rwy=A('FDR Takeoff Runway'),
               toff_rolls=S('Takeoff Roll')):

        if ambiguous_runway(rwy):
            return
        if gspd:
            speed = repair_mask(gspd.array, gspd.frequency)
        else:
            speed = repair_mask(aspd.array, aspd.frequency)
        for roll in toff_rolls:
            rot_idx = roll.stop_edge
            rot_end = runway_distance_from_end(rwy.value,
                                               lat.array[rot_idx],
                                               lon.array[rot_idx])

            lift_speed = value_at_index(speed, rot_idx) * KTS_TO_MPS
            mu = (lift_speed**2.0) / (2.0 * GRAVITY_METRIC * rot_end)
            self.create_kpv(rot_idx, mu)

"""
This KPV was sketched out following Emirates' presentation, but requires a
value for V1 which is not currently set up as a derived (or recorded)
parameter.

class DecelerationToAbortTakeoffBeforeV1(KeyPointValueNode):
    '''
    FDS developed this KPV following the 2nd EOFDM conference.
    '''

    units = 'g'

    def derive(self, lat=P('Latitude Smoothed'),
               lon=P('Longitude Smoothed'),
               gspd=P('Groundspeed'),
               aspd=P('Airspeed True'),
               v1=A('V1'),
               rwy=A('FDR Takeoff Runway'),
               toff_rolls=S('Takeoff Roll')):

        if ambiguous_runway(rwy):
            return
        if gspd:
            speed=gspd.array
        else:
            speed=aspd.array
        for roll in toff_rolls:
            v1_idx = v1.value
            rot_end = runway_distance_from_end(rwy.value,
                                               lat.array[v1_idx ],
                                               lon.array[v1_idx ])

            v1_mps = value_at_index(speed, v1.value) * KTS_TO_MPS
            mu = (v1_mps**2.0) / (2.0 * GRAVITY_METRIC * rot_end)
            self.create_kpv(vi_idx, mu)
"""
            
##############################################################################
# Runway Distances at Landing

class DistancePastGlideslopeAntennaToTouchdown(KeyPointValueNode):
    
    units = 'm'
    
    def derive(self, lat_tdn=KPV('Latitude Smoothed At Touchdown'),
               lon_tdn=KPV('Longitude Smoothed At Touchdown'),
               tdwns=KTI('Touchdown'),rwy=A('FDR Landing Runway'),
               ils_ldgs=S('ILS Localizer Established')):

        if ambiguous_runway(rwy) or not lat_tdn or not lon_tdn:
            return
        last_tdwn = tdwns.get_last()
        if not last_tdwn:
            return
        land_idx = last_tdwn.index
        # Check we did do an ILS approach (i.e. the ILS frequency was correct etc).
        if ils_ldgs.get(containing_index=land_idx):
            # OK, now do the geometry...
            gs = runway_distance_from_end(rwy.value, point='glideslope')
            td = runway_distance_from_end(rwy.value, lat_tdn.get_last().value,
                                          lon_tdn.get_last().value)
            if gs and td:
                distance = gs - td
                self.create_kpv(land_idx, distance)


class DistanceFromRunwayStartToTouchdown(KeyPointValueNode):
    '''
    Finds the distance from the runway start location to the touchdown point.
    This only operates for the last landing, and previous touch and goes will
    not be recorded.
    '''
    
    units = 'm'
    
    def derive(self, lat_tdn=KPV('Latitude Smoothed At Touchdown'),
               lon_tdn=KPV('Longitude Smoothed At Touchdown'),
               tdwns=KTI('Touchdown'),
               rwy=A('FDR Landing Runway')):

        if ambiguous_runway(rwy) or not lat_tdn or not lon_tdn:
            return

        distance_to_start = runway_distance_from_end(rwy.value, point='start')
        distance_to_tdn = runway_distance_from_end(rwy.value,
                                                   lat_tdn.get_last().value,
                                                   lon_tdn.get_last().value)
        if distance_to_tdn < distance_to_start: # sanity check
            self.create_kpv(tdwns.get_last().index,
                            distance_to_start-distance_to_tdn)


class DistanceFromTouchdownToRunwayEnd(KeyPointValueNode):
    '''
    Finds the distance from the touchdown point to the end of the runway
    hardstanding. This only operates for the last landing, and previous touch
    and goes will not be recorded.
    '''
    
    units = 'm'
    
    def derive(self, lat_tdn=KPV('Latitude Smoothed At Touchdown'),
               lon_tdn=KPV('Longitude Smoothed At Touchdown'),
               tdwns=KTI('Touchdown'),
               rwy=A('FDR Landing Runway')):

        if ambiguous_runway(rwy) or not lat_tdn or not tdwns:
            return

        distance_to_tdn = runway_distance_from_end(rwy.value,
                                                   lat_tdn.get_last().value,
                                                   lon_tdn.get_last().value)
        self.create_kpv(tdwns.get_last().index, distance_to_tdn)


class DecelerationFromTouchdownToStopOnRunway(KeyPointValueNode):
    '''
    This determines the average level of deceleration required to stop the
    aircraft before reaching the end of the runway. It takes into account the
    length of the runway, the point of touchdown and the groundspeed at the
    point of touchdown.

    The numerical value is in units of g, and can be compared with surface
    conditions or autobrake settings. For example, if the value is 0.14 and
    the braking is "medium" (typically 0.1g) it is likely that the aircraft
    will overrun the runway if the pilot relies upon wheel brakes alone.

    The value will vary during the deceleration phase, but the highest value
    was found to arise at or very shortly after touchdown, as the aerodynamic
    and rolling drag at high speed normally exceed this level. Therefore for
    simplicity we just use the value at touchdown.
    '''
    def derive(self, gspd=P('Groundspeed'), tdwns=S('Touchdown'), landings=S('Landing'),
               lat_tdn=KPV('Latitude Smoothed At Touchdown'),
               lon_tdn=KPV('Longitude Smoothed At Touchdown'),
               rwy=A('FDR Landing Runway'),
               ils_gs_apps=S('ILS Glideslope Established'),
               ils_loc_apps=S('ILS Localizer Established'),
               precise=A('Precise Positioning')):
        if ambiguous_runway(rwy):
            return
        index = tdwns.get_last().index
        for landing in landings:
            if not is_index_within_slice(index, landing.slice):
                continue

            # Was this an ILS approach where the glideslope was captured?
            ils_approach = False
            for ils_loc_app in ils_loc_apps:
                if not slices_overlap(ils_loc_app.slice, landing.slice):
                    continue
                for ils_gs_app in ils_gs_apps:
                    if slices_overlap(ils_loc_app.slice, ils_gs_app.slice):
                        ils_approach = True

            # So for captured ILS approaches or aircraft with precision location we can compute the deceleration required.
            if (precise.value or ils_approach) and lat_tdn != []:
                distance_at_tdn = \
                    runway_distance_from_end(rwy.value,
                                             lat_tdn.get_last().value,
                                             lon_tdn.get_last().value)
                speed = value_at_index(repair_mask(gspd.array),index) * KTS_TO_MPS
                mu = (speed*speed) / (2.0 * GRAVITY_METRIC * (distance_at_tdn))
                self.create_kpv(index, mu)


class RunwayHeadingTrue(KeyPointValueNode):
    '''
    Calculate Runway headings from runway information dictionaries.
    '''
    @classmethod
    def can_operate(cls, available):
        return (all_of(['FDR Takeoff Runway', 'Liftoff'], available) or
                'Approach Information' in available)
    
    def derive(self, takeoff_runway=A('FDR Takeoff Runway'),
               liftoffs=KTI('Liftoff'), apps=App('Approach Information')):
        if takeoff_runway and liftoffs:
            liftoff = liftoffs.get_first()
            if liftoff:
                self.create_kpv(liftoff.index,
                                runway_heading(takeoff_runway.value))
        if apps:
            for app in apps:
                if not app.runway:
                    continue
                # Q: Is the midpoint of the slice a sensible index?
                index = (app.slice.start + 
                         ((app.slice.stop - app.slice.start) / 2))
                self.create_kpv(index, runway_heading(app.runway))


class RunwayOverrunWithoutSlowingDuration(KeyPointValueNode):
    '''
    This determines the minimum time that the aircraft will take to reach the
    end of the runway without further braking. It takes into account the
    reducing groundspeed and distance to the end of the runway.

    The numerical value is in units of seconds.

    The value will decrease if the aircraft is not decelerating
    progressively. Therefore the lower values arise if the pilot allows the
    aircraft to roll down the runway without reducing speed. It will reflect
    the reduction in margins where aircraft roll at high speed towards
    taxiways near the end of the runway, and the value relates to the time
    available to the pilot.
    '''
    def derive(self, gspd=P('Groundspeed'), tdwns=S('Touchdown'),
               landings=S('Landing'), lat = P('Latitude Smoothed'),
               lon = P('Longitude Smoothed'),
               lat_tdn=KPV('Latitude Smoothed At Touchdown'),
               lon_tdn=KPV('Longitude Smoothed At Touchdown'),
               rwy=A('FDR Landing Runway'),
               ils_gs_apps=S('ILS Glideslope Established'),
               ils_loc_apps=S('ILS Localizer Established'),
               precise=A('Precise Positioning'),
               turnoff=KTI('Landing Turn Off Runway')):
        if ambiguous_runway(rwy):
            return
        last_tdwn = tdwns.get_last()
        if not last_tdwn:
            return
        for landing in landings:
            if not is_index_within_slice(last_tdwn.index, landing.slice):
                continue
            # Was this an ILS approach where the glideslope was captured?
            ils_approach = False
            for ils_loc_app in ils_loc_apps:
                if not slices_overlap(ils_loc_app.slice, landing.slice):
                    continue
                for ils_gs_app in ils_gs_apps:
                    if slices_overlap(ils_loc_app.slice, ils_gs_app.slice):
                        ils_approach = True
            # When did we turn off the runway?
            last_turnoff = turnoff.get_last()
            if not is_index_within_slice(last_turnoff.index, landing.slice):
                continue
            # So the period of interest is...
            land_roll = slice(last_tdwn.index, last_turnoff.index)
            # So for captured ILS approaches or aircraft with precision location we can compute the deceleration required.
            if precise.value or ils_approach:
                speed = gspd.array[land_roll] * KTS_TO_MPS
                if precise.value:
                    _, dist_to_end = bearings_and_distances(
                        lat.array[land_roll],
                        lon.array[land_roll],
                        rwy.value['end'])
                    time_to_end = dist_to_end / speed
                else:
                    distance_at_tdn = runway_distance_from_end(
                        rwy.value, lat_tdn.get_last().value,
                        lon_tdn.get_last().value)
                    dist_from_td = integrate(gspd.array[land_roll],
                                             gspd.hz, scale=KTS_TO_MPS)
                    time_to_end = (distance_at_tdn - dist_from_td) / speed
                limit_point = np.ma.argmin(time_to_end)
                if limit_point < 0.0: # Some error conditions lead to rogue negative results.
                    continue
                limit_time = time_to_end[limit_point]
                self.create_kpv(limit_point + last_tdwn.index, limit_time)


class DistanceOnLandingFrom60KtToRunwayEnd(KeyPointValueNode):
    
    units = 'm'
    
    def derive(self, gspd=P('Groundspeed'),
               lat=P('Latitude Smoothed'),lon=P('Longitude Smoothed'),
               tdwns=KTI('Touchdown'),rwy=A('FDR Landing Runway')):
        if ambiguous_runway(rwy):
            return
        last_tdwn = tdwns.get_last()
        if not last_tdwn:
            return
        land_idx = last_tdwn.index
        idx_60 = index_at_value(gspd.array, 60.0, slice(land_idx, None))
        if idx_60 and rwy.value and 'start' in rwy.value:
            # Only work out the distance if we have a reading at 60kts...
            distance = runway_distance_from_end(rwy.value,
                                                lat.array[idx_60],
                                                lon.array[idx_60])
            self.create_kpv(idx_60, distance) # Metres


class HeadingDuringTakeoff(KeyPointValueNode):
    '''
    We take the median heading during the takeoff roll only as this avoids
    problems when turning onto the runway or with drift just after liftoff.
    The value is "assigned" to a time midway through the takeoff roll.
    '''

    units = 'deg'

    def derive(self,
               hdg=P('Heading Continuous'),
               takeoffs=S('Takeoff Roll')):

        for takeoff in takeoffs:
            if takeoff.slice.start and takeoff.slice.stop:
                index = (takeoff.slice.start + takeoff.slice.stop) / 2.0
                value = np.ma.median(hdg.array[takeoff.slice])
                self.create_kpv(index, value % 360.0)

class HeadingTrueDuringTakeoff(KeyPointValueNode):
    '''
    We take the median true heading during the takeoff roll only as this avoids
    problems when turning onto the runway or with drift just after liftoff.
    The value is "assigned" to a time midway through the takeoff roll.
    '''

    units = 'deg'

    def derive(self,
               hdg_true=P('Heading True Continuous'),
               takeoffs=S('Takeoff Roll')):

        for takeoff in takeoffs:
            if takeoff.slice.start and takeoff.slice.stop:
                index = (takeoff.slice.start + takeoff.slice.stop) / 2.0
                value = np.ma.median(hdg_true.array[takeoff.slice])
                self.create_kpv(index, value % 360.0)


class HeadingDuringLanding(KeyPointValueNode):
    '''
    We take the median heading during the landing roll as this avoids problems
    with drift just before touchdown and heading changes when turning off the
    runway. The value is "assigned" to a time midway through the landing phase.
    '''

    units = 'deg'

    def derive(self,
               hdg=P('Heading Continuous'),
               landings=S('Landing Roll')):

        for landing in landings:
            # Check the slice is robust.
            if landing.slice.start and landing.slice.stop:
                index = (landing.slice.start + landing.slice.stop) / 2.0
                value = np.ma.median(hdg.array[landing.slice])
                self.create_kpv(index, value % 360.0)


class HeadingTrueDuringLanding(KeyPointValueNode):
    '''
    We take the median heading true during the landing roll as this avoids
    problems with drift just before touchdown and heading changes when turning
    off the runway. The value is "assigned" to a time midway through the
    landing phase.
    '''

    units = 'deg'

    def derive(self,
               hdg=P('Heading True Continuous'),
               landings=S('Landing Roll')):

        for landing in landings:
            # Check the slice is robust.
            if landing.slice.start and landing.slice.stop:
                index = (landing.slice.start + landing.slice.stop) / 2.0
                value = np.ma.median(hdg.array[landing.slice])
                self.create_kpv(index, value % 360.0)


class HeadingAtLowestAltitudeDuringApproach(KeyPointValueNode):
    '''
    The approach phase has been found already. Here we take the heading at the
    lowest point reached in the approach.
    '''

    units = 'deg'

    def derive(self,
               hdg=P('Heading Continuous'),
               low_points=KTI('Lowest Altitude During Approach')):

        self.create_kpvs_at_ktis(hdg.array % 360.0, low_points)


class ElevatorDuringLandingMin(KeyPointValueNode):
    units = 'deg'

    def derive(self,
               elev=P('Elevator'),
               landing=S('Landing')):
        self.create_kpvs_within_slices(elev.array, landing, min_value)


##############################################################################
# Height Loss


class HeightLossLiftoffTo35Ft(KeyPointValueNode):
    '''
    At these low altitudes, the aircraft is in ground effect, so we use an
    inertial vertical speed to identify small height losses. This means that
    the algorithm will still work with low sample rate (or even missing)
    radio altimeters.
    '''

    def derive(self,
               vs=P('Vertical Speed Inertial'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for climb in alt_aal.slices_from_to(0, 35):
            array = np.ma.masked_greater_equal(vs.array[climb], 0.0)
            drops = np.ma.clump_unmasked(array)
            for drop in drops:
                ht_loss = integrate(vs.array[drop], vs.frequency)
                # Only interested in peak value - by definition the last value:
                if ht_loss[-1]:
                    self.create_kpv(drop.stop, abs(ht_loss[-1]))


class HeightLoss35To1000Ft(KeyPointValueNode):
    '''
    '''

    def derive(self,
               ht_loss=P('Descend For Flight Phases'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for climb in alt_aal.slices_from_to(35, 1000):
            index, value = min_value(ht_loss.array, climb)
            # Only report a positive value where height is lost:
            if index and value < 0:
                self.create_kpv(index, abs(value))


class HeightLoss1000To2000Ft(KeyPointValueNode):
    '''
    '''

    def derive(self,
               ht_loss=P('Descend For Flight Phases'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for climb in alt_aal.slices_from_to(1000, 2000):
            index, value = min_value(ht_loss.array, climb)
            # Only report a positive value where height is lost:
            if index and value < 0:
                self.create_kpv(index, abs(value))


##############################################################################
# ILS


class ILSFrequencyDuringApproach(KeyPointValueNode):
    '''
    Determine the ILS frequency during approach.

    The period when the aircraft was continuously established on the ILS and
    descending to the minimum point on the approach is already defined as a
    flight phase. This KPV just picks up the frequency tuned at that point.
    '''

    name = 'ILS Frequency During Approach'
    units = 'MHz'

    def derive(self,
               ils_frq=P('ILS Frequency'),
               loc_ests=S('ILS Localizer Established')):

        for loc_est in loc_ests:
            # Find the ILS frequency for the final period of operation of the
            # ILS during this approach. Note that median picks the value most
            # commonly recorded, so allows for some masked values and perhaps
            # one or two rogue values. If, however, all the ILS frequency data
            # is masked, no KPV is created.
            frequency = np.ma.median(ils_frq.array[loc_est.slice])
            if frequency:
                # Set the KPV index to the start of this ILS approach:
                self.create_kpv(loc_est.slice.start, frequency)


class ILSGlideslopeDeviation1500To1000FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the glideslope between 1500 and 1000 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Glideslope Deviation 1500 To 1000 Ft Max'
    units = 'dots'

    def derive(self,
               ils_glideslope=P('ILS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Glideslope Established')):

        alt_bands = alt_aal.slices_from_to(1500, 1000)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_glideslope.array,
            ils_bands,
            max_abs_value,
        )


class ILSGlideslopeDeviation1000To500FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the glideslope between 1000 and 500 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Glideslope Deviation 1000 To 500 Ft Max'
    units = 'dots'

    def derive(self,
               ils_glideslope=P('ILS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Glideslope Established')):

        alt_bands = alt_aal.slices_from_to(1000, 500)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_glideslope.array,
            ils_bands,
            max_abs_value,
        )


class ILSGlideslopeDeviation500To200FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the glideslope between 500 and 200 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Glideslope Deviation 500 To 200 Ft Max'
    units = 'dots'

    def derive(self,
               ils_glideslope=P('ILS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Glideslope Established')):

        alt_bands = alt_aal.slices_from_to(500, 200)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_glideslope.array,
            ils_bands,
            max_abs_value,
        )


class ILSLocalizerDeviation1500To1000FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the localizer between 1500 and 1000 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Localizer Deviation 1500 To 1000 Ft Max'
    units = 'dots'

    def derive(self,
               ils_localizer=P('ILS Localizer'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Localizer Established')):

        alt_bands = alt_aal.slices_from_to(1500, 1000)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_localizer.array,
            ils_bands,
            max_abs_value,
        )


class ILSLocalizerDeviation1000To500FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the localizer between 1000 and 500 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Localizer Deviation 1000 To 500 Ft Max'
    units = 'dots'

    def derive(self,
               ils_localizer=P('ILS Localizer'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Localizer Established')):

        alt_bands = alt_aal.slices_from_to(1000, 500)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_localizer.array,
            ils_bands,
            max_abs_value,
        )


class ILSLocalizerDeviation500To200FtMax(KeyPointValueNode):
    '''
    Determine maximum deviation from the localizer between 500 and 200 ft.

    Find where the maximum (absolute) deviation occured and store the actual
    value. We can do abs on the statistics to normalise this, but retaining the
    sign will make it possible to look for direction of errors at specific
    airports.
    '''

    name = 'ILS Localizer Deviation 500 To 200 Ft Max'
    units = 'dots'

    def derive(self,
               ils_localizer=P('ILS Localizer'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               ils_ests=S('ILS Localizer Established')):

        alt_bands = alt_aal.slices_from_to(500, 200)
        ils_bands = slices_and(alt_bands, ils_ests.get_slices())
        self.create_kpvs_within_slices(
            ils_localizer.array,
            ils_bands,
            max_abs_value,
        )


class ILSLocalizerDeviationAtTouchdown(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    Excursions - Landing (Lateral) Lateral deviation at touchdown from
    Localiser Tricky to determine how close to runway edge using localiser
    parameter as there are variable runway widths and different localiser
    beam centreline error margins for different approach categories. ILS
    Localizer Deviation At Touchdown Measurements at <2 deg pitch after main
    gear TD."

    The ILS Established period may not last until touchdown, so it is
    artificially extended by a minute to ensure coverage of the touchdown
    instant.
    '''

    name = 'ILS Localizer Deviation At Touchdown'
    units = 'dots'

    def derive(self,
               ils_localizer=P('ILS Localizer'),
               ils_ests=S('ILS Localizer Established'),
               tdwns=KTI('Touchdown')):

        for ils_est in ils_ests:
            for tdwn in tdwns:
                ext_end = ils_est.slice.stop + ils_localizer.frequency * 60.0
                ils_est_ext = slice(ils_est.slice.start, ext_end)
                if not is_index_within_slice(tdwn.index, ils_est_ext):
                    continue
                deviation = value_at_index(ils_localizer.array, tdwn.index)
                self.create_kpv(tdwn.index, deviation)


class IANGlidepathDeviationMax(KeyPointValueNode):
    '''

    '''
    name = "IAN Glidepath Deviation"
    NAME_FORMAT = "IAN Glidepath Deviation %(max_alt)d To %(min_alt)s Ft Max"
    NAME_VALUES = {
        'max_alt' : (1500, 1000, 500),
        'min_alt' : (1000,  500, 200),
        }

    units = 'dots'

    def derive(self,
               ian_glidepath=P('IAN Glidepath'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               apps=App('Approach Information'),
               app_src_capt=P('Displayed App Source (Capt)'),
               app_src_fo=P('Displayed App Source (FO)')):

        # Displayed App Source required to ensure that IAN is being followed
        in_fmc = (app_src_capt.array == 'FMC') | (app_src_fo.array == 'FMC')
        ian_glidepath.array[~in_fmc] = np.ma.masked

        for app in apps:
            if app.gs_est:
                # Mask IAN data for approaches where ILS is established
                ian_glidepath.array[app.slice] = np.ma.masked

        for idx in range(len(self.NAME_VALUES['max_alt'])):
            max_alt = self.NAME_VALUES['max_alt'][idx]
            min_alt = self.NAME_VALUES['min_alt'][idx]
            alt_bands = alt_aal.slices_from_to(max_alt, min_alt)

            ian_est_bands = []
            for band in alt_bands:
                ian_glide_est = scan_ils('glideslope', ian_glidepath.array,
                                         alt_aal.array, band,
                                         ian_glidepath.frequency)
                if ian_glide_est:
                    ian_est_bands.append(ian_glide_est)

            self.create_kpvs_within_slices(
                ian_glidepath.array,
                ian_est_bands,
                max_abs_value,
                max_alt=max_alt,
                min_alt=min_alt
            )
            # End for


class IANFinalApproachCourseDeviationMax(KeyPointValueNode):
    '''

    '''
    name = "IAN Final Approach Course Deviation"
    NAME_FORMAT = "IAN Final Approach Course Deviation %(max_alt)d To %(min_alt)s Ft Max"
    NAME_VALUES = {
        'max_alt' : (1500, 1000, 500),
        'min_alt' : (1000,  500, 200),
        }

    units = 'dots'

    def derive(self,
               ian_final=P('IAN Final Approach Course'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               apps=App('Approach Information'),
               app_src_capt=M('Displayed App Source (Capt)'),
               app_src_fo=M('Displayed App Source (FO)')):

        # Displayed App Source required to ensure that IAN is being followed
        in_fmc = (app_src_capt.array == 'FMC') | (app_src_fo.array == 'FMC')
        ian_final.array[~in_fmc] = np.ma.masked

        for app in apps:
            if app.loc_est:
                # Mask IAN data for approaches where ILS is established
                ian_final.array[app.slice] = np.ma.masked

        for idx in range(len(self.NAME_VALUES['max_alt'])):
            max_alt = self.NAME_VALUES['max_alt'][idx]
            min_alt = self.NAME_VALUES['min_alt'][idx]

            alt_bands = alt_aal.slices_from_to(max_alt, min_alt)

            ian_est_bands = []
            for band in alt_bands:
                final_app_course_est = scan_ils('glideslope', ian_final.array,
                                                alt_aal.array, band,
                                                ian_final.frequency)
                if final_app_course_est:
                    ian_est_bands.append(final_app_course_est)

            self.create_kpvs_within_slices(
                ian_final.array,
                ian_est_bands,
                max_abs_value,
                max_alt=max_alt,
                min_alt=min_alt
            )


##############################################################################


class IsolationValveOpenAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = ''

    def derive(self,
               isol=M('Isolation Valve Open'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(isol.array.raw, liftoffs, suppress_zeros=True)


class PackValvesOpenAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = ''

    def derive(self,
               pack=M('Pack Valves Open'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(pack.array.raw, liftoffs, suppress_zeros=True)


##############################################################################
# Latitude/Longitude


########################################
# Helpers


def calculate_runway_midpoint(rwy):
    '''
    Attempts to calculate the runway midpoint data provided in the AFR.

    1. If there are no runway start coordinates, use the runway end coordinates
    2. If there are no runway end coordinates, use the runway start coordinates
    3. Attempt to calculate the midpoint of the great circle path between them.
    '''
    rwy_s = rwy.get('start', {})
    rwy_e = rwy.get('end', {})
    lat_s = rwy_s.get('latitude')
    lat_e = rwy_e.get('latitude')
    lon_s = rwy_s.get('longitude')
    lon_e = rwy_e.get('longitude')
    if lat_s is None or lon_s is None:
        return (lat_e, lon_e)
    if lat_e is None or lon_e is None:
        return (lat_s, lon_s)
    return midpoint(lat_s, lon_s, lat_e, lon_e)


########################################
# Latitude/Longitude @ Takeoff/Landing


class LatitudeAtTouchdown(KeyPointValueNode):
    '''
    Latitude and Longitude at Touchdown.

    The position of the landing is recorded in the form of KPVs as this is
    used in a number of places. From the touchdown moments, the raw latitude
    and longitude data is used to create the *AtTouchdown parameters, and these
    are in turn used to compute the landing attributes.

    Once the landing attributes (especially the runway details) are known,
    the positional data can be smoothed using ILS data or (if this is a
    non-precision approach) the known touchdown aiming point. With more
    accurate positional data the touchdown point can be computed more
    accurately.

    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return 'Touchdown' in available and any_of(('Latitude',
                                                    'Latitude (Coarse)',
                                                    'AFR Landing Runway',
                                                    'AFR Landing Airport'),
                                                   available)

    def derive(self,
            lat=P('Latitude'),
            tdwns=KTI('Touchdown'),
            land_afr_apt=A('AFR Landing Airport'),
            land_afr_rwy=A('AFR Landing Runway'),
            lat_c=P('Latitude (Coarse)')):
        '''
        Note that Latitude (Coarse) is a superframe parameter with poor
        resolution recorded on some FDAUs. Keeping it at the end of the list
        of parameters means that it will be aligned to a higher sample rate
        rather than dragging other parameters down to its sample rate. See
        767 Delta data frame.
        '''
        # 1. Attempt to use latitude parameter if available:
        if lat:
            self.create_kpvs_at_ktis(lat.array, tdwns)
            return

        if lat_c:
            for tdwn in tdwns:
                # Touchdown may be masked for Coarse parameter.
                self.create_kpv(
                    tdwn.index,
                    closest_unmasked_value(lat_c.array, tdwn.index).value,
                )
            return

        value = None

        # 2a. Attempt to use latitude of runway midpoint:
        if value is None and land_afr_rwy:
            lat_m, lon_m = calculate_runway_midpoint(land_afr_rwy.value)
            value = lat_m

        # 2b. Attempt to use latitude of airport:
        if value is None and land_afr_apt:
            value = land_afr_apt.value.get('latitude')

        if value is not None:
            self.create_kpv(tdwns[-1].index, value)
            return

        # XXX: Is there something else we can do here other than fail?
        raise Exception('Unable to determine a latitude at touchdown.')


class LongitudeAtTouchdown(KeyPointValueNode):
    '''
    Latitude and Longitude at Touchdown.

    The position of the landing is recorded in the form of KPVs as this is
    used in a number of places. From the touchdown moments, the raw latitude
    and longitude data is used to create the *AtTouchdown parameters, and these
    are in turn used to compute the landing attributes.

    Once the landing attributes (especially the runway details) are known,
    the positional data can be smoothed using ILS data or (if this is a
    non-precision approach) the known touchdown aiming point. With more
    accurate positional data the touchdown point can be computed more
    accurately.

    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return 'Touchdown' in available and any_of(('Longitude',
                                                    'Longitude (Coarse)',
                                                    'AFR Landing Runway',
                                                    'AFR Landing Airport'),
                                                   available)

    def derive(self,
            lon=P('Longitude'),
            tdwns=KTI('Touchdown'),
            land_afr_apt=A('AFR Landing Airport'),
            land_afr_rwy=A('AFR Landing Runway'),
            lon_c=P('Longitude (Coarse)')):
        '''
        See note relating to coarse latitude and longitude under Latitude At Touchdown
        '''
        # 1. Attempt to use longitude parameter if available:
        if lon:
            self.create_kpvs_at_ktis(lon.array, tdwns)
            return

        if lon_c:
            for tdwn in tdwns:
                # Touchdown may be masked for Coarse parameter.
                self.create_kpv(
                    tdwn.index,
                    closest_unmasked_value(lon_c.array, tdwn.index).value,
                )
            return

        value = None

        # 2a. Attempt to use longitude of runway midpoint:
        if value is None and land_afr_rwy:
            lat_m, lon_m = calculate_runway_midpoint(land_afr_rwy.value)
            value = lon_m

        # 2b. Attempt to use longitude of airport:
        if value is None and land_afr_apt:
            value = land_afr_apt.value.get('longitude')

        if value is not None:
            self.create_kpv(tdwns[-1].index, value)
            return

        # XXX: Is there something else we can do here other than fail?
        raise Exception('Unable to determine a longitude at touchdown.')


class LatitudeAtLiftoff(KeyPointValueNode):
    '''
    Latitude and Longitude at Liftoff.

    The position of the takeoff is recorded in the form of KPVs as this is
    used in a number of places. From the liftoff moments, the raw latitude
    and longitude data is used to create the *AtLiftoff parameters, and these
    are in turn used to compute the takeoff attributes.

    Once the takeoff attributes (especially the runway details) are known,
    the positional data can be smoothed the known liftoff point. With more
    accurate positional data the liftoff point can be computed more accurately.

    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        return 'Liftoff' in available and any_of(('Latitude',
                                                  'Latitude (Coarse)',
                                                  'AFR Takeoff Runway', 
                                                  'AFR Takeoff Airport'),
                                                 available)

    def derive(self,
            lat=P('Latitude'),
            liftoffs=KTI('Liftoff'),
            toff_afr_apt=A('AFR Takeoff Airport'),
            toff_afr_rwy=A('AFR Takeoff Runway'),
            lat_c=P('Latitude (Coarse)')):
        '''
        Note that Latitude Coarse is a superframe parameter with poor
        resolution recorded on some FDAUs. Keeping it at the end of the list
        of parameters means that it will be aligned to a higher sample rate
        rather than dragging other parameters down to its sample rate. See
        767 Delta data frame.
        '''
        # 1. Attempt to use latitude parameter if available:
        if lat:
            self.create_kpvs_at_ktis(lat.array, liftoffs)
            return
        
        if lat_c:
            self.create_kpvs_at_ktis(lat_c.array, liftoffs)
            return

        value = None

        # 2a. Attempt to use latitude of runway midpoint:
        if value is None and toff_afr_rwy:
            lat_m, lon_m = calculate_runway_midpoint(toff_afr_rwy.value)
            value = lat_m

        # 2b. Attempt to use latitude of airport:
        if value is None and toff_afr_apt:
            value = toff_afr_apt.value.get('latitude')

        if value is not None:
            self.create_kpv(liftoffs[0].index, value)
            return

        # XXX: Is there something else we can do here other than fail?
        raise Exception('Unable to determine a latitude at liftoff.')


class LongitudeAtLiftoff(KeyPointValueNode):
    '''
    Latitude and Longitude at Liftoff.

    The position of the takeoff is recorded in the form of KPVs as this is
    used in a number of places. From the liftoff moments, the raw latitude
    and longitude data is used to create the *AtLiftoff parameters, and these
    are in turn used to compute the takeoff attributes.

    Once the takeoff attributes (especially the runway details) are known,
    the positional data can be smoothed the known liftoff point. With more
    accurate positional data the liftoff point can be computed more accurately.

    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        return 'Liftoff' in available and any_of(('Longitude',
                                                  'Longitude (Coarse)',
                                                  'AFR Takeoff Runway',
                                                  'AFR Takeoff Airport'),
                                                 available)
    
    def derive(self,
            lon=P('Longitude'),
            liftoffs=KTI('Liftoff'),
            toff_afr_apt=A('AFR Takeoff Airport'),
            toff_afr_rwy=A('AFR Takeoff Runway'),
            lon_c=P('Longitude (Coarse)')):
        '''
        See note relating to coarse latitude and longitude under Latitude At Takeoff
        '''
        # 1. Attempt to use longitude parameter if available:
        if lon:
            self.create_kpvs_at_ktis(lon.array, liftoffs)
            return

        if lon_c:
            self.create_kpvs_at_ktis(lon_c.array, liftoffs)
            return
            
        value = None

        # 2a. Attempt to use longitude of runway midpoint:
        if value is None and toff_afr_rwy:
            lat_m, lon_m = calculate_runway_midpoint(toff_afr_rwy.value)
            value = lon_m

        # 2b. Attempt to use longitude of airport:
        if value is None and toff_afr_apt:
            value = toff_afr_apt.value.get('longitude')

        if value is not None:
            self.create_kpv(liftoffs[0].index, value)
            return

        # XXX: Is there something else we can do here other than fail?
        raise Exception('Unable to determine a longitude at liftoff.')


########################################
# Latitude/Longitude @ Liftoff/Touchdown


class LatitudeSmoothedAtTouchdown(KeyPointValueNode):
    '''
    Latitude and Longitude at Touchdown.

    The position of the landing is recorded in the form of KPVs as this is
    used in a number of places. From the touchdown moments, the raw latitude
    and longitude data is used to create the *AtTouchdown parameters, and these
    are in turn used to compute the landing attributes.

    Once the landing attributes (especially the runway details) are known,
    the positional data can be smoothed using ILS data or (if this is a
    non-precision approach) the known touchdown aiming point. With more
    accurate positional data the touchdown point can be computed more
    accurately.
    '''

    units = 'deg'

    def derive(self, lat=P('Latitude Smoothed'), tdwns=KTI('Touchdown')):
        '''
        '''
        self.create_kpvs_at_ktis(lat.array, tdwns)


class LongitudeSmoothedAtTouchdown(KeyPointValueNode):
    '''
    Latitude and Longitude at Touchdown.

    The position of the landing is recorded in the form of KPVs as this is
    used in a number of places. From the touchdown moments, the raw latitude
    and longitude data is used to create the *AtTouchdown parameters, and these
    are in turn used to compute the landing attributes.

    Once the landing attributes (especially the runway details) are known,
    the positional data can be smoothed using ILS data or (if this is a
    non-precision approach) the known touchdown aiming point. With more
    accurate positional data the touchdown point can be computed more
    accurately.
    '''

    units = 'deg'

    def derive(self, lon=P('Longitude Smoothed'), tdwns=KTI('Touchdown')):
        '''
        '''
        self.create_kpvs_at_ktis(lon.array, tdwns)


class LatitudeSmoothedAtLiftoff(KeyPointValueNode):
    '''
    Latitude and Longitude at Liftoff.

    The position of the takeoff is recorded in the form of KPVs as this is
    used in a number of places. From the liftoff moments, the raw latitude
    and longitude data is used to create the *AtLiftoff parameters, and these
    are in turn used to compute the takeoff attributes.

    Once the takeoff attributes (especially the runway details) are known,
    the positional data can be smoothed the known liftoff point. With more
    accurate positional data the liftoff point can be computed more accurately.
    '''

    units = 'deg'

    def derive(self, lat=P('Latitude Smoothed'), liftoffs=KTI('Liftoff')):
        '''
        '''
        self.create_kpvs_at_ktis(lat.array, liftoffs)


class LongitudeSmoothedAtLiftoff(KeyPointValueNode):
    '''
    Latitude and Longitude at Liftoff.

    The position of the takeoff is recorded in the form of KPVs as this is
    used in a number of places. From the liftoff moments, the raw latitude
    and longitude data is used to create the *AtLiftoff parameters, and these
    are in turn used to compute the takeoff attributes.

    Once the takeoff attributes (especially the runway details) are known,
    the positional data can be smoothed the known liftoff point. With more
    accurate positional data the liftoff point can be computed more accurately.
    '''

    units = 'deg'

    def derive(self, lon=P('Longitude Smoothed'), liftoffs=KTI('Liftoff')):
        '''
        '''
        self.create_kpvs_at_ktis(lon.array, liftoffs)


#########################################
# Latitude/Longitude @ Lowest Point on approach. Used to identify airport
# and runway, so that this works for both landings and aborted approaches /
# go-arounds.

class LatitudeAtLowestAltitudeDuringApproach(KeyPointValueNode):
    '''
    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    def derive(self,
               lat=P('Latitude Prepared'),
               low_points=KTI('Lowest Altitude During Approach')):

        self.create_kpvs_at_ktis(lat.array, low_points)


class LongitudeAtLowestAltitudeDuringApproach(KeyPointValueNode):
    '''
    Note: Cannot use smoothed position as this causes circular dependancy.
    '''

    units = 'deg'

    def derive(self,
               lon=P('Longitude Prepared'),
               low_points=KTI('Lowest Altitude During Approach')):

        self.create_kpvs_at_ktis(lon.array, low_points)


##############################################################################
# Mach


########################################
# Mach: General


class MachMax(KeyPointValueNode):
    '''
    '''

    units = 'Mach'

    def derive(self,
               mach=P('Mach'),
               airs=S('Airborne')):

        self.create_kpvs_within_slices(mach.array, airs, max_value)


class MachDuringCruiseAvg(KeyPointValueNode):
    '''
    '''

    units = 'Mach'

    def derive(self,
               mach=P('Mach'),
               cruises=S('Cruise')):
        
        for _slice in cruises.get_slices():
            self.create_kpv(_slice.start + (_slice.stop - _slice.start) / 2,
                            np.ma.mean(mach.array[_slice]))


########################################
# Mach: Flap


class MachWithFlapMax(KeyPointValueNode, FlapOrConfigurationMaxOrMin):
    '''
    '''

    # Note: We must use %s not %d as we've encountered a flap of 17.5 degrees.
    NAME_FORMAT = 'Mach With Flap %(flap)s Max'
    NAME_VALUES = NAME_VALUES_FLAP

    units = 'kt'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Mach', 'Fast'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               mach=P('Mach'),
               scope=S('Fast')):
        flap = flap_lever or flap_synth
        # Fast scope traps flap changes very late on the approach and raising
        # flaps before 80kn on the landing run.
        data = self.flap_or_conf_max_or_min(flap, mach, max_value, scope=scope)
        for index, value, detent in data:
            self.create_kpv(index, value, flap=detent)


########################################
# Mach: Landing Gear


class MachWithGearDownMax(KeyPointValueNode):
    '''
    '''

    units = 'Mach'

    def derive(self,
               mach=P('Mach'),
               gear=M('Gear Down'),
               airs=S('Airborne')):

        gear.array[gear.array != 'Down'] = np.ma.masked
        gear_downs = np.ma.clump_unmasked(gear.array)
        self.create_kpvs_within_slices(
            mach.array, slices_and(airs.get_slices(), gear_downs),
            max_value)


class MachWhileGearRetractingMax(KeyPointValueNode):
    '''
    '''

    units = 'Mach'

    def derive(self,
               mach=P('Mach'),
               gear_ret=S('Gear Retracting')):

        self.create_kpvs_within_slices(mach.array, gear_ret, max_value)


class MachWhileGearExtendingMax(KeyPointValueNode):
    '''
    '''

    units = 'Mach'

    def derive(self,
               mach=P('Mach'),
               gear_ext=S('Gear Extending')):

        self.create_kpvs_within_slices(mach.array, gear_ext, max_value)


##############################################################################
# Magnetic Variation


class MagneticVariationAtTakeoffTurnOntoRunway(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               mag_var=P('Magnetic Variation'),
               takeoff_turn_on_rwy=KTI('Takeoff Turn Onto Runway')):

        self.create_kpvs_at_ktis(mag_var.array, takeoff_turn_on_rwy)


class MagneticVariationAtLandingTurnOffRunway(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               mag_var=P('Magnetic Variation'),
               landing_turn_off_rwy=KTI('Landing Turn Off Runway')):

        self.create_kpvs_at_ktis(mag_var.array, landing_turn_off_rwy)


##############################################################################
# Engine Bleed


class EngBleedValvesAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = ''

    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Eng (1) Bleed',
            'Eng (2) Bleed',
            'Liftoff',
        ), available)

    def derive(self,
               liftoffs=KTI('Liftoff'),
               b1=M('Eng (1) Bleed'),
               b2=M('Eng (2) Bleed'),
               b3=M('Eng (3) Bleed'),
               b4=M('Eng (4) Bleed')):

        bleeds = vstack_params_where_state((b1, 'Open'),
                                           (b2, 'Open'),
                                           (b3, 'Open'),
                                           (b4, 'Open')).any(axis=0)
        self.create_kpvs_at_ktis(bleeds, liftoffs)


##############################################################################
# Engine EPR


class EngEPRDuringApproachMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Approach Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               approaches=S('Approach')):

        self.create_kpv_from_slices(eng_epr_max.array, approaches, max_value)


class EngEPRDuringApproachMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Approach Min'
    units = '%'

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min'),
               approaches=S('Approach')):

        self.create_kpv_from_slices(eng_epr_min.array, approaches, min_value)


class EngEPRDuringTaxiMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Taxi Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_epr_max.array, taxiing, max_value)


class EngEPRDuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_epr_max.array, ratings, max_value)


class EngTPRDuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng TPR During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_tpr_limit=P('Eng TPR Limit Difference'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_tpr_limit.array, ratings, max_value)


class EngEPRDuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_epr_max.array, ratings, max_value)


class EngTPRDuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng TPR During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_tpr_limit=P('Eng TPR Limit Difference'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_tpr_limit.array, ratings, max_value)


class EngEPRDuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_epr_max.array, slices, max_value)


class EngTPRDuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    Originally coded for 787, but the event has been disabled since it lacks a
    limit.
    '''

    name = 'Eng TPR During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_tpr_max=P('Eng (*) TPR Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_tpr_max.array, slices, max_value)


class EngEPR500To50FtMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR 500 To 50 Ft Max'
    units = '%'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_epr_max.array,
            alt_aal.slices_from_to(500, 50),
            max_value,
        )


class EngEPR500To50FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR 500 To 50 Ft Min'
    units = '%'

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_epr_min.array,
            alt_aal.slices_from_to(500, 50),
            min_value,
        )


class EngEPRFor5Sec500To50FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR For 5 Sec 500 To 50 Ft Min'
    units = '%'

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min For 5 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            eng_epr_min.array,
            trim_slices(alt_aal.slices_from_to(500, 50), 5, self.frequency,
                        duration.value),
            min_value,
        )


class EngEPRFor5Sec1000To500FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR For 5 Sec 1000 To 500 Ft Min'
    units = '%'

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min For 5 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            eng_epr_min.array,
            trim_slices(alt_aal.slices_from_to(1000, 500), 5, self.frequency,
                        duration.value),
            min_value,
        )


class EngEPRAtTOGADuringTakeoffMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng EPR At TOGA During Takeoff Max'

    def derive(self,
               eng_epr_max=P('Eng (*) EPR Max'),
               toga=M('Takeoff And Go Around'),
               takeoff=S('Takeoff')):

        indexes = find_edges_on_state_change('TOGA', toga.array,
                                             change='entering', phase=takeoff)
        for index in indexes:
            value = value_at_index(eng_epr_max.array, index)
            self.create_kpv(index, value)


class EngTPRAtTOGADuringTakeoffMin(KeyPointValueNode):
    '''
    Originally coded for 787, but the event has been disabled since it lacks a
    limit.
    '''

    name = 'Eng TPR At TOGA During Takeoff Min'

    def derive(self,
               eng_tpr_max=P('Eng (*) TPR Min'),
               toga=M('Takeoff And Go Around'),
               takeoff=S('Takeoff')):

        indexes = find_edges_on_state_change('TOGA', toga.array,
                                             change='entering', phase=takeoff)
        for index in indexes:
            value = value_at_index(eng_tpr_max.array, index)
            self.create_kpv(index, value)


##############################################################################
# Engine Fire


class EngFireWarningDuration(KeyPointValueNode):
    '''
    Duration that the any of the Engine Fire Warnings are active.
    '''

    units = 's'

    def derive(self, eng_fire=M('Eng (*) Fire'), airborne=S('Airborne')):
        self.create_kpvs_where(eng_fire.array == 'Fire',
                               eng_fire.hz, phase=airborne)


##############################################################################
# APU Fire


class APUFireWarningDuration(KeyPointValueNode):
    '''
    Duration that the any of the APU Fire Warnings are active.
    '''

    name = 'APU Fire Warning Duration'
    units = 's'

    @classmethod
    def can_operate(cls, available):
        params = ('Fire APU Single Bottle System', 'Fire APU Dual Bottle System')
        return any_of(params, available)

    def derive(self, single_bottle=M('Fire APU Single Bottle System'),
               dual_bottle=M('Fire APU Dual Bottle System')):

        hz = single_bottle.hz or dual_bottle.hz
        apu_fires = vstack_params_where_state((single_bottle, 'Fire'),
                                              (dual_bottle, 'Fire'))

        self.create_kpvs_where(apu_fires.any(axis=0) == True,
                               hz)


##############################################################################
# Engine Gas Temperature


class EngGasTempDuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_egt_max.array, ratings, max_value)


class EngGasTempDuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_egt_max.array, ratings, max_value)


class EngGasTempDuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    We assume maximum continuous power applies whenever takeoff or go-around
    power settings are not in force. So, by collecting all the high power
    periods and inverting these from the start of the first airborne section to
    the end of the last, we have the required periods of flight.
    '''

    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               airborne=S('Airborne')):

        if not airborne:
            return
        high_power_ratings = to_ratings.get_slices() + ga_ratings.get_slices()
        max_cont_rating = slices_not(
            high_power_ratings,
            begin_at=min(air.slice.start for air in airborne),
            end_at=max(air.slice.stop for air in airborne),
        )
        self.create_kpvs_within_slices(
            eng_egt_max.array,
            max_cont_rating,
            max_value,
        )


class EngGasTempDuringMaximumContinuousPowerForXMinMax(KeyPointValueNode):
    '''
    We assume maximum continuous power applies whenever takeoff or go-around
    power settings are not in force. So, by collecting all the high power
    periods and inverting these from the start of the first airborne section to
    the end of the last, we have the required periods of flight.
    '''

    NAME_FORMAT = 'Eng Gas Temp During Maximum Continuous Power For %(minutes)d Min Max'
    NAME_VALUES = {'minutes': [3, 5]}
    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               airborne=S('Airborne')):

        if not airborne:
            return
        high_power_ratings = to_ratings.get_slices() + ga_ratings.get_slices()
        max_cont_rating = slices_not(
            high_power_ratings,
            begin_at=min(air.slice.start for air in airborne),
            end_at=max(air.slice.stop for air in airborne),
        )
        for minutes in self.NAME_VALUES['minutes']:
            self.create_kpvs_within_slices(
                clip(eng_egt_max.array, minutes * 60, eng_egt_max.hz),
                max_cont_rating,
                max_value,
                minutes=minutes,
            )


class EngGasTempDuringEngStartMax(KeyPointValueNode):
    '''
    One key point value for maximum engine gas temperature at engine start for
    all engines. The value is taken from the engine with the largest value.
    '''

    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               eng_n2_min=P('Eng (*) N2 Min'),
               toff_turn_rwy=KTI('Takeoff Turn Onto Runway')):

        # We never see engine start if data started after aircraft is airborne:
        if not toff_turn_rwy:
            return

        # Where the egt is in a superframe, let's give up now:
        if eng_egt_max.frequency < 0.25:
            return

        # Extract the index for the first turn onto the runway:
        fto_idx = toff_turn_rwy.get_first().index

        # Mask out sections with N2 > 60%, i.e. all engines running:
        n2_data = eng_n2_min.array[0:fto_idx]
        n2_data[n2_data > 60.0] = np.ma.masked
        chunks = np.ma.clump_unmasked(n2_data)

        if not chunks:
            return

        self.create_kpvs_within_slices(eng_egt_max.array, chunks, max_value)


class EngGasTempDuringEngStartForXSecMax(KeyPointValueNode):
    '''
    One key point value for maximum engine gas temperature at engine start for
    all engines. The value is taken from the engine with the largest value.
    '''

    NAME_FORMAT = 'Eng Gas Temp During Eng Start For %(seconds)d Sec Max'
    NAME_VALUES = {'seconds': [5, 10, 40]}
    units = 'C'

    def derive(self,
               eng_egt_max=P('Eng (*) Gas Temp Max'),
               eng_n2_min=P('Eng (*) N2 Min'),
               toff_turn_rwy=KTI('Takeoff Turn Onto Runway')):

        # We never see engine start if data started after aircraft is airborne:
        if not toff_turn_rwy:
            return

        # Where the egt is in a superframe, let's give up now:
        if eng_egt_max.frequency < 0.25:
            return

        # Extract the index for the first turn onto the runway:
        fto_idx = toff_turn_rwy.get_first().index

        # Mask out sections with N2 > 60%, i.e. all engines running:
        n2_data = eng_n2_min.array[0:fto_idx]
        n2_data[n2_data > 60.0] = np.ma.masked
        chunks = np.ma.clump_unmasked(n2_data)

        for seconds in self.NAME_VALUES['seconds']:

            # Remove chunks of data that are too small to clip:
            slices = slices_remove_small_slices(chunks, seconds, eng_egt_max.hz)
            if not slices:
                continue

            array = clip(eng_egt_max.array, seconds, hz=eng_egt_max.hz)
            self.create_kpvs_within_slices(array, slices, max_value, seconds=seconds)


class EngGasTempDuringFlightMin(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control. In flight engine shut down."

    To detect a possible engine shutdown in flight, we look for the minimum
    gas temperature recorded during the flight. The event will then be computed
    later, testing against a suitable minimum value for a running engine.

    Note that the gas temperature can increase on an engine run down.
    '''

    units = 'C'

    def derive(self,
               eng_egt_min=P('Eng (*) Gas Temp Min'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(
            eng_egt_min.array,
            airborne,
            min_value,
        )


##############################################################################
# Engine N1


class EngN1DuringTaxiMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 During Taxi Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_n1_max.array, taxiing, max_value)


class EngN1DuringApproachMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 During Approach Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               approaches=S('Approach')):

        self.create_kpv_from_slices(eng_n1_max.array, approaches, max_value)


class EngN1DuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n1_max.array, ratings, max_value)


class EngN1DuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n1_max.array, ratings, max_value)


class EngN1DuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_n1_max.array, slices, max_value)


class EngN1CyclesDuringFinalApproach(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 Cycles During Final Approach'
    units = 'cycles'

    def derive(self,
               eng_n1_avg=P('Eng (*) N1 Avg'),
               fin_apps=S('Final Approach')):

        for fin_app in fin_apps:
            self.create_kpv(*cycle_counter(
                eng_n1_avg.array[fin_app.slice],
                5.0, 10.0, eng_n1_avg.hz,
                fin_app.slice.start,
            ))


class EngN1500To50FtMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 500 To 50 Ft Max'
    units = '%'

    def derive(self,
               eng_n1_max=P('Eng (*) N1 Max'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_n1_max.array,
            alt_aal.slices_from_to(500, 50),
            max_value,
        )


class EngN1500To50FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 500 To 50 Ft Min'
    units = '%'

    def derive(self,
               eng_n1_min=P('Eng (*) N1 Min'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_n1_min.array,
            alt_aal.slices_from_to(500, 50),
            min_value,
        )


class EngN1For5Sec500To50FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 For 5 Sec 500 To 50 Ft Min'
    units = '%'

    def derive(self,
               eng_n1_min=P('Eng (*) N1 Min For 5 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            eng_n1_min.array,
            trim_slices(alt_aal.slices_from_to(500, 50), 5, self.frequency,
                        duration.value),
            min_value,
        )


class EngN1For5Sec1000To500FtMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng N1 For 5 Sec 1000 To 500 Ft Min'
    units = '%'

    def derive(self,
               eng_n1_min=P('Eng (*) N1 Min For 5 Sec'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               duration=A('HDF Duration')):

        self.create_kpvs_within_slices(
            eng_n1_min.array,
            trim_slices(alt_aal.slices_from_to(1000, 500), 5, self.frequency,
                        duration.value),
            min_value,
        )


class EngN1WithThrustReversersInTransitMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) Asymmetric selection or achieved."
    '''

    name = 'Eng N1 With Thrust Reversers In Transit Max'
    units = '%'

    def derive(self,
               eng_n1_avg=P('Eng (*) N1 Avg'),
               tr=M('Thrust Reversers'),
               landings=S('Landing')):

        slices = [s.slice for s in landings]
        slices = clump_multistate(tr.array, 'In Transit', slices)
        self.create_kpv_from_slices(eng_n1_avg.array, slices, max_value)


# NOTE: Was named 'Eng N1 Cooldown Duration'.
# TODO: Similar KPV for duration between engine under 60 percent and engine shutdown
class EngN1Below60PercentAfterTouchdownDuration(KeyPointValueNode):
    '''
    Max duration N1 below 60% after Touchdown for engine cooldown. Using 60%
    allows for cooldown after use of Reverse Thrust.

    Evaluated for each engine to account for single engine taxi-in.

    Note: Assumes that all Engines are recorded at the same frequency.
    '''

    NAME_FORMAT = 'Eng (%(number)d) N1 Below 60 Percent After Touchdown Duration'
    NAME_VALUES = NAME_VALUES_ENGINE
    units = 's'

    @classmethod
    def can_operate(cls, available):
        return all((
            any_of(('Eng (%d) N1' % n for n in range(1, 5)), available),
            'Eng Stop' in available,
            'Touchdown' in available,
        ))

    def derive(self,
               engines_stop=KTI('Eng Stop'),
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1'),
               tdwn=KTI('Touchdown')):

        if not tdwn:
            return
        for eng_num, eng in enumerate((eng1, eng2, eng3, eng4), start=1):
            if eng is None:
                continue  # Engine is not available on this aircraft.
            eng_stop = engines_stop.get(name='Eng (%d) Stop' % eng_num)
            if not eng_stop:
                # XXX: Should we measure until the end of the flight anyway?
                # (Probably not.)
                self.debug('Engine %d did not stop on this flight, cannot '
                           'measure KPV', eng_num)
                continue
            last_tdwn_idx = tdwn.get_last().index
            last_eng_stop_idx = eng_stop[-1].index
            if last_tdwn_idx > last_eng_stop_idx:
                self.debug('Engine %d was stopped before last touchdown', eng_num)
                continue
            eng_array = repair_mask(eng.array)
            eng_below_60 = np.ma.masked_greater(eng_array, 60)
            # Measure duration between final touchdown and engine stop:
            touchdown_to_stop_slice = max_continuous_unmasked(
                eng_below_60, slice(last_tdwn_idx, last_eng_stop_idx))
            if touchdown_to_stop_slice:
                # TODO: Future storage of slice: self.slice = touchdown_to_stop_slice
                touchdown_to_stop_duration = (touchdown_to_stop_slice.stop - \
                                        touchdown_to_stop_slice.start) / self.hz
                self.create_kpv(touchdown_to_stop_slice.start,
                                touchdown_to_stop_duration, number=eng_num)
            else:
                # Create KPV of 0 seconds:
                self.create_kpv(last_eng_stop_idx, 0.0, number=eng_num)


class EngN1AtTOGADuringTakeoff(KeyPointValueNode):
    '''

    '''

    name = 'Eng N1 At TOGA During Takeoff'

    def derive(self,
               eng_n1=P('Eng (*) N1 Min'),
               toga=M('Takeoff And Go Around'),
               takeoff=S('Takeoff')):

        indexes = find_edges_on_state_change('TOGA', toga.array, change='entering', phase=takeoff)
        for index in indexes:
            value = value_at_index(eng_n1.array, index)
            self.create_kpv(index, value)


class EngN154to72PercentWithThrustReversersDeployedDurationMax(KeyPointValueNode):
    '''
    KPV created at customer request following Service Bullitin from Rolls Royce
    (TAY-72-A1771)
    '''

    NAME_FORMAT = 'Eng (%(number)d) N1 54 To 72 Percent With Thrust Reversers Deployed Duration Max'
    NAME_VALUES = NAME_VALUES_ENGINE

    units = 's'

    @classmethod
    def can_operate(cls, available):
        return all((
            any_of(('Eng (%d) N1' % n for n in cls.NAME_VALUES['number']), available),
            'Thrust Reversers' in available,
        ))

    def derive(self, eng1_n1=P('Eng (1) N1'), eng2_n1=P('Eng (2) N1'),
               eng3_n1=P('Eng (3) N1'), eng4_n1=P('Eng (4) N1'),
               tr=M('Thrust Reversers'),):

        eng_n1_list = (eng1_n1, eng2_n1, eng3_n1, eng4_n1)
        reverser_deployed = np.ma.where(tr.array == 'Deployed', tr.array, np.ma.masked)
        for eng_num, eng_n1 in enumerate(eng_n1_list, 1):
            if not eng_n1:
                continue
            n1_range = np.ma.masked_outside(eng_n1.array, 54, 72)
            n1_range.mask = n1_range.mask | reverser_deployed.mask
            max_slice = max_continuous_unmasked(n1_range)
            if max_slice:
                self.create_kpvs_from_slice_durations((max_slice,),
                                                      eng_n1.frequency,
                                                      number=eng_num)


##############################################################################
# Engine N2


class EngN2DuringTaxiMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N2 During Taxi Max'
    units = '%'

    def derive(self,
               eng_n2_max=P('Eng (*) N2 Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_n2_max.array, taxiing, max_value)


class EngN2DuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N2 During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n2_max=P('Eng (*) N2 Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n2_max.array, ratings, max_value)


class EngN2DuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N2 During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n2_max=P('Eng (*) N2 Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n2_max.array, ratings, max_value)


class EngN2DuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N2 During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_n2_max=P('Eng (*) N2 Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_n2_max.array, slices, max_value)


class EngN2CyclesDuringFinalApproach(KeyPointValueNode):
    '''
    '''

    name = 'Eng N2 Cycles During Final Approach'
    units = 'cycles'

    def derive(self,
               eng_n2_avg=P('Eng (*) N2 Avg'),
               fin_apps=S('Final Approach')):

        for fin_app in fin_apps:
            self.create_kpv(*cycle_counter(
                eng_n2_avg.array[fin_app.slice],
                10.0, 10.0, eng_n2_avg.hz,
                fin_app.slice.start,
            ))


##############################################################################
# Engine N3


class EngN3DuringTaxiMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N3 During Taxi Max'
    units = '%'

    def derive(self,
               eng_n3_max=P('Eng (*) N3 Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_n3_max.array, taxiing, max_value)


class EngN3DuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N3 During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n3_max=P('Eng (*) N3 Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n3_max.array, ratings, max_value)


class EngN3DuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N3 During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_n3_max=P('Eng (*) N3 Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_n3_max.array, ratings, max_value)


class EngN3DuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng N3 During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_n3_max=P('Eng (*) N3 Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_n3_max.array, slices, max_value)


##############################################################################
# Engine Np


class EngNpDuringClimbMin(KeyPointValueNode):
    '''
    '''

    name = 'Eng Np During Climb Min'
    units = '%'

    def derive(self,
               eng_Np_min=P('Eng (*) Np Min'),
               climbs=S('Climbing')):

        self.create_kpv_from_slices(eng_Np_min.array, climbs, min_value)


class EngNpDuringTaxiMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Np During Taxi Max'
    units = '%'

    def derive(self,
               eng_Np_max=P('Eng (*) Np Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_Np_max.array, taxiing, max_value)


class EngNpDuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Np During Takeoff 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_Np_max=P('Eng (*) Np Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_Np_max.array, ratings, max_value)


class EngNpDuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Np During Go Around 5 Min Rating Max'
    units = '%'

    def derive(self,
               eng_Np_max=P('Eng (*) Np Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_Np_max.array, ratings, max_value)


class EngNpDuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Np During Maximum Continuous Power Max'
    units = '%'

    def derive(self,
               eng_Np_max=P('Eng (*) Np Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_Np_max.array, slices, max_value)


##############################################################################
# Engine Throttles


class ThrottleReductionToTouchdownDuration(KeyPointValueNode):
    '''
    Records the duration from touchdown until Throttle leaver is reduced in
    seconds, negative seconds indicates throttle reduced before touchdown.

    The original algorithm used reduction through 18deg throttle angle, but
    in cases where little power is being applied it was found that the
    throttle lever may not reach this setting. Also, this implies an
    aircraft-dependent threshold which would be difficult to maintain, and
    requires consistent throttle lever sensor rigging which may not be
    reliable on some types.

    For these reasons the algorithm has been adapted to use the peak
    curvature technique, scanning from 5 seconds before the start of the
    landing (passing 50ft) to the minimum throttle setting prior to
    application of reverse thrust.
    '''

    units = 's'

    def derive(self,
               tla=P('Throttle Levers'),
               landings=S('Landing'),
               touchdowns=KTI('Touchdown'),
               eng_n1=P('Eng (*) N1 Avg'),
               frame=A('Frame')):

        dt = 5/tla.hz # 5 second counter
        for landing in landings:
            for touchdown in touchdowns.get(within_slice=landing.slice):
                begin = landing.slice.start-dt
                # Seek the throttle reduction before thrust reverse is applied:
                scope = slice(begin, landing.slice.stop)
                dn1 = rate_of_change_array(eng_n1.array[scope], eng_n1.hz)
                dtla = rate_of_change_array(tla.array[scope], tla.hz)
                dboth = dn1*dtla
                peak_decel = np.ma.argmax(dboth)
                reduced_scope = slice(begin, landing.slice.start+peak_decel)
                # Now see where the power is reduced:
                reduce_idx = peak_curvature(tla.array, reduced_scope,
                                            curve_sense='Convex', gap=1, ttp=3)
                if reduce_idx:
                    
                    '''
                    import matplotlib.pyplot as plt
                    plt.plot(eng_n1.array[scope])
                    plt.plot(tla.array[scope])
                    plt.plot(reduce_idx-begin, eng_n1.array[reduce_idx],'db')
                    output_dir = os.path.join('C:\\Users\\Dave Jesse\\FlightDataRunner\\test_data\\88-Results\\', 
                                              'Throttle reduction graphs'+frame.name)
                    if not os.path.exists(output_dir):
                        os.mkdir(output_dir)
                    plt.savefig(os.path.join(output_dir, frame.value + ' '+ str(int(reduce_idx)) +'.png'))
                    plt.clf()
                    print int(reduce_idx)
                    '''
                    
                    if reduce_idx:
                        value = (reduce_idx - touchdown.index) / tla.hz
                        self.create_kpv(reduce_idx, value)


################################################################################
# Engine Vib Broadband


class EngVibBroadbandMax(KeyPointValueNode):
    '''
    '''
    
    def derive(self, eng_vib_max=P('Eng (*) Vib Broadband Max')):
        self.create_kpv(*max_value(eng_vib_max.array))


##############################################################################
# Engine Oil Pressure


class EngOilPressMax(KeyPointValueNode):
    '''
    '''

    units = 'psi'

    def derive(self,
               oil_press=P('Eng (*) Oil Press Max')):

        self.create_kpv(*max_value(oil_press.array))


class EngOilPressMin(KeyPointValueNode):
    '''
    '''

    units = 'psi'

    def derive(self,
               oil_press=P('Eng (*) Oil Press Min'),
               airborne=S('Airborne')):

        # Only in flight to avoid zero pressure readings for stationary engines.
        self.create_kpvs_within_slices(oil_press.array, airborne, min_value)


##############################################################################
# Engine Oil Quantity


class EngOilQtyMax(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               oil_qty=P('Eng (*) Oil Qty Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(oil_qty.array, airborne, max_value)


class EngOilQtyMin(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               oil_qty=P('Eng (*) Oil Qty Min'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(oil_qty.array, airborne, min_value)


class EngOilQtyDuringTaxiInMax(KeyPointValueNode):
    '''
    '''

    NAME_FORMAT = "Eng (%(number)s) Oil Qty During Taxi In Max"
    NAME_VALUES = NAME_VALUES_ENGINE

    units = 'kg'

    @classmethod
    def can_operate(cls, available):
        return any_of(
            ('Eng (1) Oil Qty', 'Eng (2) Oil Qty', 'Eng (3) Oil Qty',
             'Eng (4) Oil Qty'), available
        ) and 'Taxi In' in available

    def derive(self,
               oil_qty1=P('Eng (1) Oil Qty'),
               oil_qty2=P('Eng (2) Oil Qty'),
               oil_qty3=P('Eng (3) Oil Qty'),
               oil_qty4=P('Eng (4) Oil Qty'),
               taxi_in=S('Taxi In')):

        if oil_qty1:
            self.create_kpvs_within_slices(oil_qty1.array, taxi_in, max_value,
                                           number=1)

        if oil_qty2:
            self.create_kpvs_within_slices(oil_qty2.array, taxi_in, max_value,
                                           number=2)

        if oil_qty3:
            self.create_kpvs_within_slices(oil_qty3.array, taxi_in, max_value,
                                           number=3)

        if oil_qty4:
            self.create_kpvs_within_slices(oil_qty4.array, taxi_in, max_value,
                                           number=4)


class EngOilQtyDuringTaxiOutMax(KeyPointValueNode):
    '''
    '''

    NAME_FORMAT = "Eng (%(number)s) Oil Qty During Taxi Out Max"
    NAME_VALUES = NAME_VALUES_ENGINE

    units = 'kg'

    @classmethod
    def can_operate(cls, available):
        return any_of(
            ('Eng (1) Oil Qty', 'Eng (2) Oil Qty', 'Eng (3) Oil Qty',
             'Eng (4) Oil Qty'), available
        ) and 'Taxi Out' in available

    def derive(self,
               oil_qty1=P('Eng (1) Oil Qty'),
               oil_qty2=P('Eng (2) Oil Qty'),
               oil_qty3=P('Eng (3) Oil Qty'),
               oil_qty4=P('Eng (4) Oil Qty'),
               taxi_out=S('Taxi Out')):

        if oil_qty1:
            self.create_kpvs_within_slices(oil_qty1.array, taxi_out, max_value,
                                           number=1)

        if oil_qty2:
            self.create_kpvs_within_slices(oil_qty2.array, taxi_out, max_value,
                                           number=2)

        if oil_qty3:
            self.create_kpvs_within_slices(oil_qty3.array, taxi_out, max_value,
                                           number=3)

        if oil_qty4:
            self.create_kpvs_within_slices(oil_qty4.array, taxi_out, max_value,
                                           number=4)


##############################################################################
# Engine Oil Temperature


class EngOilTempMax(KeyPointValueNode):
    '''
    '''

    units = 'C'

    def derive(self,
               oil_temp=P('Eng (*) Oil Temp Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(oil_temp.array, airborne, max_value)


class EngOilTempForXMinMax(KeyPointValueNode):
    '''
    Maximum oil temperature sustained for X minutes.
    '''

    NAME_FORMAT = 'Eng Oil Temp For %(minutes)d Min Max'
    NAME_VALUES = {'minutes': [15, 20, 45]}
    units = 'C'

    def derive(self,
               oil_temp=P('Eng (*) Oil Temp Max')):

        # Some aircraft don't have oil temperature sensors fitted. This trap
        # may be superceded by masking the Eng (*) Oil Temp Max parameter in
        # future:
        if np.ma.count(oil_temp.array) == 0:
            return

        for minutes in self.NAME_VALUES['minutes']:

            oil_sustained = clip(oil_temp.array, minutes * 60, oil_temp.hz)
            # There have been cases where there were no valid oil temperature
            # measurements throughout the flight, in which case there's no
            # point testing for a maximum:
            if oil_sustained is not None:
                self.create_kpv(*max_value(oil_sustained), minutes=minutes)


##############################################################################
# Engine Torque


class EngTorqueDuringTaxiMax(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_max=P('Eng (*) Torque Max'),
               taxiing=S('Taxiing')):

        self.create_kpv_from_slices(eng_trq_max.array, taxiing, max_value)


class EngTorqueDuringTakeoff5MinRatingMax(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_max=P('Eng (*) Torque Max'),
               ratings=S('Takeoff 5 Min Rating')):

        self.create_kpvs_within_slices(eng_trq_max.array, ratings, max_value)


class EngTorqueDuringGoAround5MinRatingMax(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_max=P('Eng (*) Torque Max'),
               ratings=S('Go Around 5 Min Rating')):

        self.create_kpvs_within_slices(eng_trq_max.array, ratings, max_value)


class EngTorqueDuringMaximumContinuousPowerMax(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_max=P('Eng (*) Torque Max'),
               to_ratings=S('Takeoff 5 Min Rating'),
               ga_ratings=S('Go Around 5 Min Rating'),
               grounded=S('Grounded')):

        slices = to_ratings + ga_ratings + grounded
        self.create_kpv_outside_slices(eng_trq_max.array, slices, max_value)


class EngTorque500To50FtMax(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_max=P('Eng (*) Torque Max'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_trq_max.array,
            alt_aal.slices_from_to(500, 50),
            max_value,
        )


class EngTorque500To50FtMin(KeyPointValueNode):
    '''
    '''

    units = 'ft.lb'

    def derive(self,
               eng_trq_min=P('Eng (*) Torque Min'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            eng_trq_min.array,
            alt_aal.slices_from_to(500, 50),
            min_value,
        )


##############################################################################
# Engine Vibrations (N*)


class EngVibN1Max(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib N1 Max'
    units = ''

    def derive(self,
               eng_vib_n1=P('Eng (*) Vib N1 Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_n1.array, airborne, max_value)


class EngVibN2Max(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib N2 Max'
    units = ''

    def derive(self,
               eng_vib_n2=P('Eng (*) Vib N2 Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_n2.array, airborne, max_value)


class EngVibN3Max(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib N3 Max'
    units = ''

    def derive(self,
               eng_vib_n3=P('Eng (*) Vib N3 Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_n3.array, airborne, max_value)


# Engine Vibrations (Filters)


class EngVibAMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib A Max'
    units = ''

    def derive(self,
               eng_vib_a=P('Eng (*) Vib A Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_a.array, airborne, max_value)


class EngVibBMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib B Max'
    units = ''

    def derive(self,
               eng_vib_b=P('Eng (*) Vib B Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_b.array, airborne, max_value)


class EngVibCMax(KeyPointValueNode):
    '''
    '''

    name = 'Eng Vib C Max'
    units = ''

    def derive(self,
               eng_vib_c=P('Eng (*) Vib C Max'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(eng_vib_c.array, airborne, max_value)


##############################################################################
# Engine Shutdown


class EngShutdownDuringFlightDuration(KeyPointValueNode):
    '''
    This KPV measures the duration the engines are not all running while
    airborne - i.e. Expected engine shutdown during flight.
    
    Based upon "Eng (*) All Running" which uses the best of the available N2
    and Fuel Flow to determine whether the engines are all running.
    '''

    units = 's'

    def derive(self,
               eng_running=P('Eng (*) All Running'),
               airborne=S('Airborne')):

        eng_off = eng_running.array == 'Not Running'
        for air in airborne:
            for _slice in runs_of_ones(eng_off[air.slice]):
                dur = float(_slice.stop - _slice.start) / self.frequency
                if dur > 2:
                    # Must be at least 2 seconds not running:
                    self.create_kpv(_slice.start + air.slice.start, dur)


##############################################################################

class SingleEngineDuringTaxiInDuration(KeyPointValueNode):
    def derive(self,
               all=M('Eng (*) All Running'), any=M('Eng (*) Any Running'),
               taxi=S('Taxi In')):
        some_running = all.array ^ any.array
        self.create_kpvs_where(some_running == 1, all.hz, phase=taxi)


class SingleEngineDuringTaxiOutDuration(KeyPointValueNode):
    def derive(self,
               all=M('Eng (*) All Running'), any=M('Eng (*) Any Running'),
               taxi=S('Taxi Out')):
        some_running = all.array ^ any.array
        self.create_kpvs_where(some_running == 1, all.hz, phase=taxi)


##############################################################################


class EventMarkerPressed(KeyPointValueNode):
    def derive(self, event=P('Event Marker'), airs=S('Airborne')):
        pushed = np.ma.clump_unmasked(np.ma.masked_equal(event.array, 0))
        events_in_air = slices_and(pushed, airs.get_slices())
        for event_in_air in events_in_air:
            if event_in_air:
                duration = (event_in_air.stop - event_in_air.start) / \
                    event.frequency
                index = (event_in_air.stop + event_in_air.start) / 2.0
                self.create_kpv(index, duration)


class HeightOfBouncedLanding(KeyPointValueNode):
    '''
    This measures the peak height of the bounced landing.

    Bounced landing phase is established by looking for the maximum height
    after touching the ground while still going fast.
    '''
    def derive(self, alt = P('Altitude AAL'),
               bounced_landing=S('Bounced Landing')):
        self.create_kpvs_within_slices(alt.array, bounced_landing, max_value)


##############################################################################
# Heading


class HeadingDeviationFromRunwayAbove80KtsAirspeedDuringTakeoff(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take off (Lateral). Heading changes on runway before rotation
    commenced. During rotation on some types, the a/c may be allowed to
    weathercock into wind."

    The heading deviation is measured as the largest deviation from the runway
    centreline between 80kts airspeed and 5 deg nose pitch up, at which time
    the weight is clearly coming off the mainwheels (we avoid using weight on
    nosewheel as this is often not recorded).
    '''

    def derive(self,
               head=P('Heading True Continuous'),
               airspeed=P('Airspeed'),
               pitch=P('Pitch'),
               toffs=S('Takeoff'),
               rwy=A('FDR Takeoff Runway')):

        if ambiguous_runway(rwy):
            return
        # checks for multiple takeoffs from a single takeoff runway (rejected?)
        for toff in toffs:
            start = index_at_value(airspeed.array, 80.0, _slice=toff.slice)
            if not start:
                self.warning("'%s' did not transition through 80 kts in '%s' "
                        "slice '%s'.", airspeed.name, toffs.name, toff.slice)
                continue
            stop = index_at_value(pitch.array, 5.0, _slice=toff.slice)
            if not stop:
                self.warning("'%s' did not transition through 5 deg in '%s' "
                        "slice '%s'.", pitch.name, toffs.name, toff.slice)
                continue
            scan = slice(start, stop)
            dev = runway_deviation(head.array, rwy.value)
            index, value = max_abs_value(dev, scan)
            self.create_kpv(index, value)


class HeadingDeviationFromRunwayAtTOGADuringTakeoff(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take off (Lateral). TOGA pressed before a/c aligned."
    '''

    name = 'Heading Deviation From Runway At TOGA During Takeoff'

    def derive(self,
               head=P('Heading True Continuous'),
               toga=M('Takeoff And Go Around'),
               takeoff=S('Takeoff'),
               rwy=A('FDR Takeoff Runway')):

        if ambiguous_runway(rwy):
            return
        indexes = find_edges_on_state_change('TOGA', toga.array, phase=takeoff)
        for index in indexes:
            brg = value_at_index(head.array, index)
            dev = runway_deviation(brg, rwy.value)
            self.create_kpv(index, dev)


class HeadingDeviationFromRunwayAt50FtDuringLanding(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take off (Lateral). Crosswind. Could look at the difference
    between a/c heading and R/W heading at 50ft."
    '''

    def derive(self,
               head=P('Heading True Continuous'),
               landings=S('Landing'),
               rwy=A('FDR Landing Runway')):

        if ambiguous_runway(rwy):
            return
        # Only have runway details for final landing.
        land = landings[-1]
        # By definition, landing starts at 50ft.
        brg = closest_unmasked_value(head.array, land.start_edge).value
        dev = runway_deviation(brg, rwy.value)
        self.create_kpv(land.start_edge, dev)


class HeadingDeviationFromRunwayDuringLandingRoll(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) Heading changes on runways."
    '''

    def derive(self,
               head=P('Heading True Continuous'),
               land_rolls=S('Landing Roll'),
               rwy=A('FDR Landing Runway')):

        if ambiguous_runway(rwy):
            return

        final_landing = land_rolls[-1].slice
        dev = runway_deviation(head.array, rwy.value)
        self.create_kpv_from_slices(dev, [final_landing], max_abs_value)


class HeadingVariation300To50Ft(KeyPointValueNode):
    '''
    '''

    def derive(self,
               head=P('Heading Continuous'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for band in alt_aal.slices_from_to(300, 50):
            dev = np.ma.ptp(head.array[band])
            self.create_kpv(band.stop, dev)


class HeadingVariation500To50Ft(KeyPointValueNode):
    '''
    '''

    def derive(self,
               head=P('Heading Continuous'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for band in alt_aal.slices_from_to(500, 50):
            dev = np.ma.ptp(head.array[band])
            self.create_kpv(band.stop, dev)


class HeadingVariationAbove100KtsAirspeedDuringLanding(KeyPointValueNode):
    '''
    For landing the Altitude AAL is used to detect start of landing to avoid
    variation from the use of different aircraft recording configurations.
    '''

    def derive(self,
               head=P('Heading Continuous'),
               airspeed=P('Airspeed'),
               alt=P('Altitude AAL For Flight Phases'),
               lands=S('Landing')):

        for land in lands:
            begin = index_at_value(alt.array, 1.0, _slice=land.slice)
            end = index_at_value(airspeed.array, 100.0, _slice=land.slice)
            if begin is None or begin > end:
                # Corrupt landing slices or landed below 100kts. Can happen!
                break
            else:
                head_dev = np.ma.ptp(head.array[begin:end + 1])
                self.create_kpv((begin + end) / 2, head_dev)


class HeadingVariationTouchdownPlus4SecTo60KtsAirspeed(KeyPointValueNode):
    '''
    Maximum difference in Magnetic Heading.
    '''

    def derive(self,
               head=P('Heading Continuous'),
               airspeed=P('Airspeed'),
               tdwns=KTI('Touchdown')):

        for tdwn in tdwns:
            begin = tdwn.index + 4.0 * head.frequency
            end = index_at_value(airspeed.array, 60.0, slice(begin, None))
            if end:
                # We found a suitable endpoint, so create a KPV...
                dev = np.ma.ptp(head.array[begin:end + 1])
                self.create_kpv(end, dev)


class HeadingVacatingRunway(KeyPointValueNode):
    '''
    Heading vacating runway is only used to try to identify handed
    runways in the absence of better information. See Approaches node.
    '''

    def derive(self,
               head=P('Heading Continuous'),
               off_rwys=KTI('Landing Turn Off Runway')):

        # To save taking modulus of the entire array, we'll do this in stages.
        for off_rwy in off_rwys:
            # We try to extend the index by five seconds to make a clear
            # heading change. The KTI is at the point of turnoff at which
            # moment the heading change can be very small.
            index = min(off_rwy.index + 5, len(head.array) - 1)
            value = head.array[index] % 360.0
            self.create_kpv(index, value)


##############################################################################
# Height


class HeightMinsToTouchdown(KeyPointValueNode):
    '''
    '''

    # TODO: Review and improve this technique of building KPVs on KTIs.
    from analysis_engine.key_time_instances import MinsToTouchdown

    NAME_FORMAT = 'Height ' + MinsToTouchdown.NAME_FORMAT
    NAME_VALUES = MinsToTouchdown.NAME_VALUES
    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               mtt_kti=KTI('Mins To Touchdown')):

        for mtt in mtt_kti:
            # XXX: Assumes that the number will be the first part of the name:
            time = int(mtt.name.split(' ')[0])
            self.create_kpv(mtt.index, alt_aal.array[mtt.index], time=time)


##############################################################################
# Flap


class FlapAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               'Liftoff' in available

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               liftoffs=KTI('Liftoff')):
        flap = flap_lever or flap_synth
        self.create_kpvs_at_ktis(flap.array, liftoffs, interpolate=False)


class FlapAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               'Touchdown' in available

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               touchdowns=KTI('Touchdown')):
        flap = flap_lever or flap_synth
        self.create_kpvs_at_ktis(flap.array, touchdowns, interpolate=False)


class FlapAtGearDownSelection(KeyPointValueNode):
    '''
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               'Gear Down Selection' in available

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               gear_dn_sel=KTI('Gear Down Selection')):
        flap = flap_lever or flap_synth
        self.create_kpvs_at_ktis(flap.array, gear_dn_sel, interpolate=False)


class FlapWithGearUpMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               'Gear Down' in available

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               gear=M('Gear Down')):
        flap = flap_lever or flap_synth
        gear_up = np.ma.masked_equal(gear.array.raw, gear.array.state['Down'])
        gear_up_slices = np.ma.clump_unmasked(gear_up)
        self.create_kpvs_within_slices(flap.array, gear_up_slices, max_value)


class FlapWithSpeedbrakeDeployedMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Speedbrake Selected', 'Airborne', 'Landing'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               spd_brk=M('Speedbrake Selected'),
               airborne=S('Airborne'),
               landings=S('Landing')):
        flap = flap_lever or flap_synth
        deployed = spd_brk.array == 'Deployed/Cmd Up'
        deployed = mask_outside_slices(deployed, airborne.get_slices())
        deployed = mask_inside_slices(deployed, landings.get_slices())
        deployed_slices = runs_of_ones(deployed)
        self.create_kpv_from_slices(flap.array, deployed_slices, max_value)


class FlapAt1000Ft(KeyPointValueNode):
    '''
    Flap setting at 1000ft on approach.
    '''
    @classmethod
    def can_operate(cls, available):
        return ('Flap' in available)
    
    def derive(self, flap=M('Flap'), gates=KTI('Altitude When Descending')):
        for gate in gates:
            if gate.name=='1000 Ft Descending':
                self.create_kpvs_at_ktis(flap.array, [gate])


class FlapAt500Ft(KeyPointValueNode):
    '''
    Flap setting at 500ft on approach.
    '''
    @classmethod
    def can_operate(cls, available):
        return ('Flap' in available)
    
    def derive(self, flap=M('Flap'), gates=KTI('Altitude When Descending')):
        for gate in gates:
            if gate.name=='500 Ft Descending':
                self.create_kpvs_at_ktis(flap.array, [gate])
    
    
##############################################################################


class FlareDuration20FtToTouchdown(KeyPointValueNode):
    '''
    The Altitude Radio reference is included to make sure this KPV is not
    computed if there is no radio height reference. With small turboprops, we
    have seen 40ft pressure altitude difference between the point of
    touchdown and the landing roll, so trying to measure this 20ft to
    touchdown difference is impractical.
    '''
    def derive(self, alt_aal=P('Altitude AAL For Flight Phases'),
               tdowns=KTI('Touchdown'), lands=S('Landing'),
               ralt=P('Altitude Radio')):
        for tdown in tdowns:
            this_landing = lands.get_surrounding(tdown.index)
            if this_landing:
                # Scan backwards from touchdown to the start of the landing
                # which is defined as 50ft, so will include passing through
                # 20ft AAL.
                aal_at_tdown = value_at_index(alt_aal.array, tdown.index)
                idx_20 = index_at_value(alt_aal.array, aal_at_tdown + 20.0,
                                        _slice=slice(tdown.index,
                                                     this_landing[0].start_edge,
                                                     -1))
                if not idx_20:
                    # why not?
                    raise ValueError("Did not cross 20ft before touchdown point - sounds unlikely")
                self.create_kpv(
                    tdown.index,
                    (tdown.index - idx_20) / alt_aal.frequency)


class FlareDistance20FtToTouchdown(KeyPointValueNode):
    '''
    TODO: Write a test for this function with less than one second between 20ft and touchdown, using interval arithmetic.
    NAX_1_LN-DYC_20120104234127_22_L3UQAR___dev__sdb.001.hdf5
    '''
    def derive(self, alt_aal=P('Altitude AAL For Flight Phases'),
               tdowns=KTI('Touchdown'), lands=S('Landing'),
               gspd=P('Groundspeed')):
        for tdown in tdowns:
            this_landing = lands.get_surrounding(tdown.index)
            if this_landing:
                idx_20 = index_at_value(
                    alt_aal.array, 20.0,
                    _slice=slice(ceil(tdown.index), this_landing[0].slice.start - 1, -1))
                # Integrate returns an array, so we need to take the max
                # value to yield the KTP value.
                if idx_20:
                    dist = max(integrate(gspd.array[idx_20:tdown.index+1],
                                         gspd.hz))
                    self.create_kpv(tdown.index, dist)


##############################################################################
# Fuel Quantity


class FuelQtyAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               fuel_qty=P('Fuel Qty'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(fuel_qty.array, liftoffs)


class FuelQtyAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               fuel_qty=P('Fuel Qty'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(fuel_qty.array, touchdowns)


class FuelQtyLowWarningDuration(KeyPointValueNode):
    '''
    Measures the duration of the Fuel Quantity Low warning discretes.
    '''
    units = 's'

    def derive(self, warning=M('Fuel Qty (*) Low')):
        self.create_kpvs_where(warning.array == 'Warning', warning.hz)


class FuelJettisonDuration(KeyPointValueNode):
    '''
    '''
    units = 's'

    def derive(self,
               jet=P('Jettison Nozzle'),
               airborne=S('Airborne')):

        self.create_kpvs_where(jet.array == 'Jettison', jet.hz, phase=airborne)

##############################################################################
# Groundspeed


class GroundspeedMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               grounded=S('Grounded')):

        self.create_kpvs_within_slices(gnd_spd.array, grounded, max_value)


class GroundspeedWhileTaxiingStraightMax(KeyPointValueNode):
    '''
    Groundspeed while not turning is rarely an issue, so we compute only one
    KPV for taxi out and one for taxi in. The straight sections are identified
    by masking the turning phases and then testing the resulting data.
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               taxiing=S('Taxiing'),
               turns=S('Turning On Ground')):

        gnd_spd_array = mask_inside_slices(gnd_spd.array, turns.get_slices())
        self.create_kpvs_within_slices(gnd_spd_array, taxiing, max_value)


class GroundspeedWhileTaxiingTurnMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               taxiing=S('Taxiing'),
               turns=S('Turning On Ground')):

        gnd_spd_array = mask_outside_slices(gnd_spd.array, turns.get_slices())
        self.create_kpvs_within_slices(gnd_spd_array, taxiing, max_value)


class GroundspeedDuringRejectedTakeoffMax(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               rtos=S('Rejected Takeoff')):

        self.create_kpvs_within_slices(gnd_spd.array, rtos, max_value)


class GroundspeedAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(gnd_spd.array, touchdowns)


class GroundspeedVacatingRunway(KeyPointValueNode):
    '''
    '''

    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               off_rwy=KTI('Landing Turn Off Runway')):

        self.create_kpvs_at_ktis(gnd_spd.array, off_rwy)


class GroundspeedAtTOGA(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Selection of TOGA late in take-off
    roll."

    This KPV measures the groundspeed at the point of TOGA selection,
    irrespective of whether this is late (or early!).

    Note: Takeoff phase is used as this includes turning onto the runway
          whereas Takeoff Roll only starts after the aircraft is accelerating.
    '''

    name = 'Groundspeed At TOGA'
    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               toga=M('Takeoff And Go Around'),
               takeoffs=S('Takeoff')):

        indexes = find_edges_on_state_change('TOGA', toga.array, phase=takeoffs)
        for index in indexes:
            self.create_kpv(index, value_at_index(gnd_spd.array, index))


class GroundspeedWithThrustReversersDeployedMin(KeyPointValueNode):
    '''
    '''
    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               tr=M('Thrust Reversers'),
               power=P('Eng (*) N1 Max'),
               landings=S('Landing')):

        for landing in landings:
            high_rev = thrust_reversers_working(landing, power, tr)
            self.create_kpvs_within_slices(gnd_spd.array, high_rev, min_value)


class GroundspeedStabilizerOutOfTrimDuringTakeoffMax(KeyPointValueNode):
    '''
    Maximum Groundspeed turing takeoff roll when the stabilizer is out of trim.
    '''
    units = 'kt'

    @classmethod
    def can_operate(cls, available, family=A('Family'), series=A('Series')):
        from flightdatautilities import trim_limits

        family_ok = family \
            and family.value in trim_limits.STABILIZER_LIMITS_FAMILIES
        series_ok = series \
            and series.value in trim_limits.STABILIZER_LIMITS_SERIES

        return (family_ok or series_ok) and \
            all_of(('Groundspeed', 'Stabilizer', 'Takeoff Roll'), available)

    def derive(self,
               gnd_spd=P('Groundspeed'),
               stab=P('Stabilizer'),
               takeoff_roll=S('Takeoff Roll'),
               family=A('Family'),
               series=A('Series'),
               ):
        from flightdatautilities.trim_limits import get_stabilizer_limits

        family_name = family.value if family else None
        series_name = series.value if series else None

        stab_fwd, stab_aft = get_stabilizer_limits(family_name, series_name)

        if stab_fwd is None or stab_aft is None:
            self.warning('No stabilizer trim limits for aircraft family `%s`',
                         family.value)

        masked_in_trim = np.ma.masked_inside(stab.array, stab_fwd, stab_aft)
        # Masking groundspeed where stabilizer is in trim
        # WARNING: in this particular case we don't want the KPV to be created
        # when the condition (stabilizer out of trim) is not met.
        gspd_masked = np.ma.array(gnd_spd.array, mask=masked_in_trim.mask)
        self.create_kpvs_within_slices(gspd_masked, takeoff_roll, max_value)


class GroundspeedSpeedbrakeHandleDuringTakeoffMax(KeyPointValueNode):
    '''
    Maximum Groundspeed turing takeoff roll when the speedbrake handle is over
    limit.
    '''
    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               spdbrk=P('Speedbrake Handle'),
               takeoff_roll=S('Takeoff Roll'),
               ):

        SPEEDBRAKE_HANDLE_LIMIT = 2.0

        masked_in_range = np.ma.masked_less_equal(spdbrk.array,
                                                  SPEEDBRAKE_HANDLE_LIMIT)

        # Masking groundspeed where speedbrake is within limit.
        # WARNING: in this particular case we don't want the KPV to be created
        # when the condition (stabilizer out of trim) is not met.
        gspd_masked = np.ma.array(gnd_spd.array, mask=masked_in_range.mask)
        self.create_kpvs_within_slices(gspd_masked, takeoff_roll, max_value)


class GroundspeedSpeedbrakeDuringTakeoffMax(KeyPointValueNode):
    '''
    Maximum Groundspeed turing takeoff roll when the speedbrake handle is over
    limit.
    '''
    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               spdbrk=P('Speedbrake'),
               takeoff_roll=S('Takeoff Roll'),
               ):

        SPEEDBRAKE_LIMIT = 39

        masked_in_range = np.ma.masked_less_equal(spdbrk.array,
                                                  SPEEDBRAKE_LIMIT)

        # Masking groundspeed where speedbrake is within limit.
        # WARNING: in this particular case we don't want the KPV to be created
        # when the condition (stabilizer out of trim) is not met.
        gspd_masked = np.ma.array(gnd_spd.array, mask=masked_in_range.mask)
        self.create_kpvs_within_slices(gspd_masked, takeoff_roll, max_value)


class GroundspeedFlapChangeDuringTakeoffMax(KeyPointValueNode):
    '''
    Maximum Groundspeed turing takeoff roll when the speedbrake handle is over
    limit.
    '''
    units = 'kt'

    def derive(self,
               gnd_spd=P('Groundspeed'),
               flap=P('Flap'),
               takeoff_roll=S('Takeoff Roll'),
               ):

        flap_changes = np.ma.ediff1d(flap.array, to_begin=0)
        masked_in_range = np.ma.masked_equal(flap_changes, 0)

        # Masking groundspeed where speedbrake is within limit.
        # WARNING: in this particular case we don't want the KPV to be created
        # when the condition (stabilizer out of trim) is not met.
        gspd_masked = np.ma.array(gnd_spd.array, mask=masked_in_range.mask)
        self.create_kpvs_within_slices(gspd_masked, takeoff_roll, max_value)


##############################################################################
# Law

class AlternateLawDuration(KeyPointValueNode):
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Alternate Law', 'Pitch Alternate Law',
                       'Pitch Alternate Law (1)', 'Pitch Alternate Law (2)',
                       'Roll Alternate Law'), available)
    
    def derive(self,
               alternate_law=M('Alternate Law'),
               pitch_alternate_law=M('Pitch Alternate Law'),
               pitch_alternate_law_1=M('Pitch Alternate Law (1)'),
               pitch_alternate_law_2=M('Pitch Alternate Law (2)'),
               roll_alternate_law=M('Roll Alternate Law')):
        combined_law = vstack_params_where_state(
            (alternate_law, 'Engaged'),
            (pitch_alternate_law, 'Engaged'),
            (pitch_alternate_law_1, 'Engaged'),
            (pitch_alternate_law_2, 'Engaged'),
            (roll_alternate_law, 'Engaged')).any(axis=0)
        self.create_kpvs_from_slice_durations(runs_of_ones(combined_law),
                                              self.frequency)


class DirectLawDuration(KeyPointValueNode):
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Direct Law', 'Pitch Direct Law',
                       'Roll Direct Law'), available)
    
    def derive(self,
               direct_law=M('Direct Law'),
               pitch_direct_law=M('Pitch Direct Law'),
               roll_direct_law=M('Roll Direct Law')):
        combined_law = vstack_params_where_state(
            (direct_law, 'Engaged'),
            (pitch_direct_law, 'Engaged'),
            (roll_direct_law, 'Engaged')).any(axis=0)
        self.create_kpvs_from_slice_durations(runs_of_ones(combined_law),
                                              self.frequency)


##############################################################################
# Pitch


class PitchAfterFlapRetractionMax(KeyPointValueNode):
    '''
    FDS added this KPV during the UK CAA Significant Seven programme. "Loss
    of Control Pitch. FDS recommend addition of a maximum pitch attitude KPV,
    as this will make a good backstop to identify a number of events, such as
    control malfunctions, which from experience are often not detected by
    'normal' event algorithms.

    Normal pitch maxima occur during takeoff and in some cases over 2,000ft
    but flap retraction is a good condition to apply to avoid these normal
    maxima.
    '''

    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Pitch', 'Airborne'), available)

    def derive(self,
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               pitch=P('Pitch'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        scope = []
        for air in airborne:
            clean = np.ma.masked_greater(flap.array[air.slice], 0.0)
            slices = np.ma.clump_unmasked(clean)
            if not slices:
                continue
            scope.append(slice(air.slice.start + slices[0].start, air.slice.stop))
        self.create_kpvs_within_slices(pitch.array, scope, max_value)


class PitchAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(pitch.array, liftoffs)


class PitchAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(pitch.array, touchdowns)


class PitchAt35FtDuringClimb(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL'),
               climbs=S('Initial Climb')):

        for climb in climbs:
            value = value_at_index(pitch.array, climb.start_edge)
            self.create_kpv(climb.start_edge, value)


class PitchTakeoffMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               takeoffs=S('Takeoff')):

        self.create_kpvs_within_slices(pitch.array, takeoffs, max_value)


class Pitch35To400FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               climbs=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 35, 400)
        alt_climb_sections = valid_slices_within_array(alt_band, climbs)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_climb_sections,
            max_value,
        )


class Pitch35To400FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               climbs=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 35, 400)
        alt_climb_sections = valid_slices_within_array(alt_band, climbs)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_climb_sections,
            min_value,
        )


class Pitch400To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               climbs=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 400, 1000)
        alt_climb_sections = valid_slices_within_array(alt_band, climbs)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_climb_sections,
            max_value,
        )


class Pitch400To1000FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               climbs=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 400, 1000)
        alt_climb_sections = valid_slices_within_array(alt_band, climbs)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_climb_sections,
            min_value,
        )


class Pitch1000To500FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 500)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_app_sections,
            max_value,
        )


class Pitch1000To500FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 500)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_app_sections,
            min_value,
        )


class Pitch500To50FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 500, 50)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            pitch.array,
            alt_app_sections,
            max_value,
        )


class Pitch500To20FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_from_to(500, 20),
            min_value,
        )


class Pitch500To7FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_from_to(500, 7),
            max_value,
        )


class Pitch500To7FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_from_to(500, 7),
            min_value,
        )


class Pitch50FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_to_kti(50, touchdowns),
            max_value,
        )


class Pitch20FtToTouchdownMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_to_kti(20, touchdowns),
            min_value,
        )


class Pitch7FtToTouchdownMin(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_to_kti(7, touchdowns),
            min_value,
        )


class Pitch7FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch.array,
            alt_aal.slices_to_kti(7, touchdowns),
            max_value,
        )


class AirspeedV2Plus20DifferenceAtVNAVModeAndEngThrustModeRequired(KeyPointValueNode):
    '''
    '''
    
    units = 'kt'
    
    def derive(self,
               airspeed=P('Airspeed'),
               v2=P('V2'),
               vnav_thrusts=KTI('VNAV Mode And Eng Thrust Mode Required')):
        
        # XXX: Assuming V2 value is constant.
        v2_value = v2.array[np.ma.where(v2.array)[0][0]] + 20
        for vnav_thrust in vnav_thrusts:
            difference = abs(v2_value - airspeed.array[vnav_thrust.index])
            self.create_kpv(vnav_thrust.index, difference)


class PitchCyclesDuringFinalApproach(KeyPointValueNode):
    '''
    Counts the number of half-cycles of pitch attitude that exceed 3 deg in
    pitch from peak to peak and with a maximum cycle period of 10 seconds
    during the final approach phase.
    '''

    units = 'cycles'

    def derive(self,
               pitch=P('Pitch'),
               fin_apps=S('Final Approach')):

        for fin_app in fin_apps:
            self.create_kpv(*cycle_counter(
                pitch.array[fin_app.slice],
                3.0, 10.0, pitch.hz,
                fin_app.slice.start,
            ))


class PitchDuringGoAroundMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Mis-handled G/A - ...Rotation to 12 deg pitch..."
    '''

    units = 'deg'

    def derive(self,
               pitch=P('Pitch'),
               go_arounds=S('Go Around And Climbout')):

        self.create_kpvs_within_slices(pitch.array, go_arounds, max_value)


class PitchAtVNAVModeAndEngThrustModeRequired(KeyPointValueNode):
    '''
    Will create a Pitch KPV for each KTI.
    '''
    
    name = 'Pitch At VNAV Mode And Eng Thrust Mode Required'
    
    units = 'deg'
    
    def derive(self,
               pitch=P('Pitch'),
               vnav_thrust=KTI('VNAV Mode And Eng Thrust Mode Required')):
        
        self.create_kpvs_at_ktis(pitch.array, vnav_thrust)


##############################################################################
# Pitch Rate


class PitchRate35To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg/s'

    def derive(self,
               pitch_rate=P('Pitch Rate'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            pitch_rate.array,
            alt_aal.slices_from_to(35, 1000),
            max_value,
        )


class PitchRate20FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'deg/s'

    def derive(self,
               pitch_rate=P('Pitch Rate'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch_rate.array,
            alt_aal.slices_to_kti(20, touchdowns),
            max_value,
        )


class PitchRate20FtToTouchdownMin(KeyPointValueNode):
    '''
    '''

    units = 'deg/s'

    def derive(self,
               pitch_rate=P('Pitch Rate'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            pitch_rate.array,
            alt_aal.slices_to_kti(20, touchdowns),
            min_value,
        )


class PitchRate2DegPitchTo35FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg/s'

    def derive(self,
               pitch_rate=P('Pitch Rate'),
               two_deg_pitch_to_35ft=S('2 Deg Pitch To 35 Ft')):

        self.create_kpvs_within_slices(
            pitch_rate.array,
            two_deg_pitch_to_35ft,
            max_value,
        )


class PitchRate2DegPitchTo35FtMin(KeyPointValueNode):
    '''
    '''

    units = 'deg/s'

    def derive(self,
               pitch_rate=P('Pitch Rate'),
               two_deg_pitch_to_35ft=S('2 Deg Pitch To 35 Ft')):

        self.create_kpvs_within_slices(
            pitch_rate.array,
            two_deg_pitch_to_35ft,
            min_value,
        )


##############################################################################
# Vertical Speed (Rate of Climb/Descent) Helpers


def vert_spd_phase_max_or_min(obj, vrt_spd, phases, function):
    '''
    Vertical Speed (Rate of Climb/Descent) Helper
    '''
    for phase in phases:
        duration = phase.slice.stop - phase.slice.start
        if duration > CLIMB_OR_DESCENT_MIN_DURATION:
            index, value = function(vrt_spd.array, phase.slice)
            obj.create_kpv(index, value)


##############################################################################
# Rate of Climb


class RateOfClimbMax(KeyPointValueNode):
    '''
    In cases where the aircraft does not leave the ground, we get a descending
    phase that equates to an empty list, which is not iterable.
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               climbing=S('Climbing')):

        vert_spd_phase_max_or_min(self, vrt_spd, climbing, max_value)


class RateOfClimb35To1000FtMin(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               climbs=S('Initial Climb')):

        self.create_kpvs_within_slices(vrt_spd.array, climbs, min_value)


class RateOfClimbBelow10000FtMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Airborne Conflict (Mid-Air Collision) Excessive rates of climb/descent
    (>3,000FPM) within a TMA (defined as < 10,000ft)"
    '''
    #Q: Should this exclude go-around and climb out as defined below?
    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude STD Smoothed')):

        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_aal.slices_from_to(0, 10000),
            max_value,
        )


class RateOfClimbDuringGoAroundMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Mis-handled G/A." Concern here is excessive rates of
    climb following enthusiastic application of power and pitch up.
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               go_arounds=S('Go Around And Climbout')):

        self.create_kpvs_within_slices(vrt_spd.array, go_arounds, max_value)


##############################################################################
# Rate of Descent


class RateOfDescentMax(KeyPointValueNode):
    '''
    In cases where the aircraft does not leave the ground, we get a descending
    phase that equates to an empty list, which is not iterable.
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               descending=S('Descending')):

        vert_spd_phase_max_or_min(self, vrt_spd, descending, min_value)


class RateOfDescentTopOfDescentTo10000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude STD Smoothed'),
               descents=S('Combined Descent')):

        alt_band = np.ma.masked_less(alt_aal.array, 10000)
        alt_descent_sections = valid_slices_within_array(alt_band, descents)
        self.create_kpvs_within_slices(vrt_spd.array, alt_descent_sections, min_value)


class RateOfDescentBelow10000FtMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Airborne Conflict (Mid-Air Collision) Excessive rates of climb/descent
    (>3,000FPM) within a TMA (defined as < 10,000ft)"
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_std=P('Altitude STD Smoothed'),
               descents=S('Combined Descent')):
        alt_band = np.ma.masked_outside(alt_std.array, 0, 10000)
        alt_descent_sections = valid_slices_within_array(alt_band, descents)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_descent_sections,
            min_value
        )


class RateOfDescent10000To5000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_std=P('Altitude STD Smoothed'),
               descent=S('Descent')):

        alt_band = np.ma.masked_outside(alt_std.array, 10000, 5000)
        alt_descent_sections = valid_slices_within_array(alt_band, descent)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_descent_sections,
            min_value
        )


class RateOfDescent5000To3000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               descent=S('Descent')):

        alt_band = np.ma.masked_outside(alt_aal.array, 5000, 3000)
        alt_descent_sections = valid_slices_within_array(alt_band, descent)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_descent_sections,
            min_value
        )


class RateOfDescent3000To2000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               init_app=S('Initial Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 3000, 2000)
        alt_app_sections = valid_slices_within_array(alt_band, init_app)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_app_sections,
            min_value
        )


class RateOfDescent2000To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               init_app=S('Initial Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 2000, 1000)
        alt_app_sections = valid_slices_within_array(alt_band, init_app)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_app_sections,
            min_value
        )


class RateOfDescent1000To500FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 500)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_app_sections,
            min_value
        )


class RateOfDescent500To50FtMax(KeyPointValueNode):
    '''
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 500, 50)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_app_sections,
            min_value
        )


class RateOfDescent50FtToTouchdownMax(KeyPointValueNode):
    '''
    Rate of descent between 50ft and touchdown.

    At this altitude, Altitude AAL is sourced from Altitude Radio where one
    is available, so this is effectively 50ft Radio to touchdown.

    The ground effect compressibility makes the normal pressure altitude
    based vertical speed meaningless, so we use the more complex inertial
    computation to give accurate measurements within ground effect.
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed Inertial'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            vrt_spd.array,
            alt_aal.slices_to_kti(50, touchdowns),
            min_value,
        )


class RateOfDescentAtTouchdown(KeyPointValueNode):
    '''
    We use the inertial vertical speed to avoid ground effects and give an
    accurate value at the point of touchdown.
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed Inertial'),
               tdns=KTI('Touchdown')):
        self.create_kpvs_at_ktis(vrt_spd.array, tdns)


class RateOfDescentDuringGoAroundMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Mis-handled G/A."
    '''

    units = 'fpm'

    def derive(self,
               vrt_spd=P('Vertical Speed'),
               go_arounds=S('Go Around And Climbout')):

        self.create_kpvs_within_slices(vrt_spd.array, go_arounds, min_value)


##### TODO: Implement!
####class RateOfDescentOverGrossWeightLimitAtTouchdown(KeyPointValueNode):
####    '''
####    '''
####
####    units = 'fpm'
####
####    def derive(self, x=P('Not Yet')):
####        '''
####        '''
####        return NotImplemented


##############################################################################
# Roll


class RollLiftoffTo20FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            roll.array,
            alt_aal.slices_from_to(1, 20),
            max_abs_value,
        )


class Roll20To400FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            roll.array,
            alt_aal.slices_from_to(20, 400),
            max_abs_value,
        )


class Roll400To1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               init_climb=S('Initial Climb')):

        alt_band = np.ma.masked_outside(alt_aal.array, 400, 1000)
        alt_climb_sections = valid_slices_within_array(alt_band, init_climb)
        self.create_kpvs_within_slices(
            roll.array,
            alt_climb_sections,
            max_abs_value
        )


class RollAbove1000FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            roll.array,
            alt_aal.slices_above(1000),
            max_abs_value,
        )


class Roll1000To300FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               fin_app=S('Final Approach')):

        alt_band = np.ma.masked_outside(alt_aal.array, 1000, 300)
        alt_app_sections = valid_slices_within_array(alt_band, fin_app)
        self.create_kpvs_within_slices(
            roll.array,
            alt_app_sections,
            max_abs_value,
        )


class Roll300To20FtMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            roll.array,
            alt_aal.slices_from_to(300, 20),
            max_abs_value,
        )


class Roll20FtToTouchdownMax(KeyPointValueNode):
    '''
    '''

    units = 'deg'

    def derive(self,
               roll=P('Roll'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            roll.array,
            alt_aal.slices_to_kti(20, touchdowns),
            max_abs_value,
        )


class RollCyclesDuringFinalApproach(KeyPointValueNode):
    '''
    Counts the number of cycles of roll attitude that exceed 5 deg from
    peak to peak and with a maximum cycle period of 10 seconds during the
    final approach phase.

    The algorithm counts each half-cycle, so an "N" figure would give a value
    of 1.5 cycles.
    '''

    units = 'cycles'

    def derive(self,
               roll=P('Roll'),
               fin_apps=S('Final Approach')):

        for fin_app in fin_apps:
            self.create_kpv(*cycle_counter(
                roll.array[fin_app.slice],
                5.0, 10.0, roll.hz,
                fin_app.slice.start,
            ))


class RollCyclesNotDuringFinalApproach(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control - PIO. CAA limit > 20 deg total variation side to side".

    FDS cautioned 20 deg was excessive and evaluated different levels over 10
    sec time period with a view to settling the levels for production use.
    Having run a hundred sample flights using thresholds from 2 to 20 deg, 5
    deg was selected on the basis that this balanced enough data for trend
    analysis (a KPV was recorded for about one flight in three) without
    excessive counting of minor cycles. It was also convenient that this
    matched the existing threshold used by FDS for final approach analysis.

    Note: The algorithm counts each half-cycle, so an "N" figure would give a
    value of 1.5 cycles.
    '''

    units = 'cycles'

    def derive(self,
               roll=P('Roll'),
               airborne=S('Airborne'),
               fin_apps=S('Final Approach'),
               landings=S('Landing')):

        not_fas = slices_and_not(airborne, fin_apps)
        # TODO: Fix this:
        #not_fas = slices_and_not(not_fas, landings)
        for not_fa in not_fas:
            self.create_kpv(*cycle_counter(
                roll.array[not_fa],
                5.0, 10.0, roll.hz,
                not_fa.start,
            ))


##############################################################################
# Rudder


class RudderDuringTakeoffMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Lateral) Rudder kick/oscillations. Difficult due to
    gusts and effect of buildings."
    '''

    units = 'deg'

    def derive(self,
               rudder=P('Rudder'),
               to_rolls=S('Takeoff Roll')):

        self.create_kpvs_within_slices(rudder.array, to_rolls, max_abs_value)


class RudderCyclesAbove50Ft(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) Rudder kick/oscillations Often there
    during landing, therefore need to determine what is abnormal, which may
    be difficult."

    Looks for sharp rudder reversal. Excludes operation below 50ft as this is
    normal use of the rudder to kick off drift. Uses the standard cycle
    counting process but looking for only one pair of half-cycles.

    The threshold used to be 6.5 deg, derived from a manufacturer's document,
    but this did not provide meaningful results in routine operations, so the
    threshold was reduced to 2 deg over 2 seconds.
    '''

    units = 'cycles'

    def derive(self,
               rudder=P('Rudder'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for above_50 in alt_aal.slices_above(50.0):
            self.create_kpv(*cycle_counter(
                rudder.array[above_50],
                2.0, 2.0, rudder.hz,
                above_50.start,
            ))


class RudderReversalAbove50Ft(KeyPointValueNode):
    '''
    While Rudder Cycles Above 50 Ft looks for repeated cycles, this measures
    the amplitude of a single worst case cycle within a 3 second period. This
    can be related to fin stress resulting from rapid reversal of loads.
    '''

    units = 'deg'

    def derive(self,
               rudder=P('Rudder'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for above_50 in alt_aal.slices_above(50.0):
            self.create_kpv(*cycle_select(
                rudder.array[above_50],
                1.0, 3.0, rudder.hz,
                above_50.start,
            ))


class RudderPedalForceMax(KeyPointValueNode):
    '''
    Maximum rudder pedal force (irrespective of which foot is used !)
    '''
    units = 'lbf'

    def derive(self,
               force=P('Rudder Pedal Force'),
               fast=S('Fast')):
        self.create_kpvs_within_slices(
            force.array, fast.get_slices(),
            max_abs_value)


##############################################################################
# Speedbrake


class SpeedbrakeDeployed1000To20FtDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               spd_brk=M('Speedbrake Selected'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        for descent in alt_aal.slices_from_to(1000, 20):
            array = spd_brk.array[descent] == 'Deployed/Cmd Up'
            slices = shift_slices(runs_of_ones(array), descent.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency,
                                                  mark='start')


# TODO: Properly test this and compare with flap version above!
class SpeedbrakeDeployedWithConfDuration(KeyPointValueNode):
    '''
    Conf used here, but not tried or tested. Presuming conf 2 / conf 3 should
    not be used with speedbrakes.
    '''

    units = 's'

    def derive(self,
               spd_brk=M('Speedbrake Selected'),
               conf=P('Configuration'),
               airborne=S('Airborne')):

        for air in airborne:
            spd_brk_dep = spd_brk.array[air.slice] == 'Deployed/Cmd Up'
            conf_extend = conf.array[air.slice] >= 2.0
            array = spd_brk_dep & conf_extend
            slices = shift_slices(runs_of_ones(array), air.slice.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency,
                                                  mark='start')


class SpeedbrakeDeployedWithFlapDuration(KeyPointValueNode):
    '''
    '''

    units = 's'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Flap Lever', 'Flap'), available) and \
               all_of(('Speedbrake Selected', 'Airborne'), available)

    def derive(self,
               spd_brk=M('Speedbrake Selected'),
               flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               airborne=S('Airborne')):
        flap = flap_lever or flap_synth
        for air in airborne:
            spd_brk_dep = spd_brk.array[air.slice] == 'Deployed/Cmd Up'
            flap_extend = flap.array[air.slice] > 0
            array = spd_brk_dep & flap_extend
            slices = shift_slices(runs_of_ones(array), air.slice.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency,
                                                  mark='start')


class SpeedbrakeDeployedWithPowerOnDuration(KeyPointValueNode):
    '''
    Each time the aircraft is flown with high power and the speedbrakes open,
    something unusual is happening. We record the duration this happened for.

    The threshold for high power is 60% N1. This aligns with the Airbus AFPS.
    '''
    units = 's'

    def derive(self, spd_brk=M('Speedbrake Selected'),
               power=P('Eng (*) N1 Avg'), airborne=S('Airborne')):
        power_on_percent = 60.0
        for air in airborne:
            spd_brk_dep = spd_brk.array[air.slice] == 'Deployed/Cmd Up'
            high_power = power.array[air.slice] >= power_on_percent 
            array = spd_brk_dep & high_power
            slices = shift_slices(runs_of_ones(array), air.slice.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency,
                                                  mark='start')


class SpeedbrakeDeployedDuringGoAroundDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Mis-handled G/A - ...Speedbrake retraction."
    '''

    units = 's'

    def derive(self,
               spd_brk=M('Speedbrake Selected'),
               go_arounds=S('Go Around And Climbout')):

        deployed = spd_brk.array == 'Deployed/Cmd Up'
        for go_around in go_arounds:
            array = deployed[go_around.slice]
            slices = shift_slices(runs_of_ones(array),
                                  go_around.slice.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency,
                                                  mark='start')


##############################################################################
# Warnings: Stick Pusher/Shaker


class StickPusherActivatedDuration(KeyPointValueNode):
    '''
    We annotate the stick pusher event with the duration of the event.
    '''

    units = 's'

    def derive(self, stick_pusher=M('Stick Pusher'), airs=S('Airborne')):
        # TODO: Check that this triggers correctly as stick push events are probably
        #       single samples.
        self.create_kpvs_where(stick_pusher.array == 'Push',
                               stick_pusher.hz, phase=airs)


class StickShakerActivatedDuration(KeyPointValueNode):
    '''
    We annotate the stick shaker event with the duration of the event.
    '''

    units = 's'

    def derive(self, stick_shaker=M('Stick Shaker'), airs=S('Airborne')):
        self.create_kpvs_where(stick_shaker.array == 'Shake',
                               stick_shaker.hz, phase=airs)


class OverspeedDuration(KeyPointValueNode):
    '''
    '''
    units = 's'

    def derive(self, overspeed=M('Overspeed Warning')):
        self.create_kpvs_where(overspeed.array == 'Overspeed', self.frequency)


##############################################################################
# Tail Clearance


class TailClearanceDuringTakeoffMin(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_tail=P('Altitude Tail'),
               takeoffs=S('Takeoff')):

        self.create_kpvs_within_slices(alt_tail.array, takeoffs, min_value)


class TailClearanceDuringLandingMin(KeyPointValueNode):
    '''
    '''

    units = 'ft'

    def derive(self,
               alt_tail=P('Altitude Tail'),
               landings=S('Landing')):

        self.create_kpvs_within_slices(alt_tail.array, landings, min_value)


class TailClearanceDuringApproachMin(KeyPointValueNode):
    '''
    This finds abnormally low tail clearance during the approach down to 100ft.
    It searches for the minimum angular separation between the flightpath and
    the terrain, so a 500ft clearance at 2500ft AAL is considered more
    significant than 500ft at 1500ft AAL. The value stored is the tail
    clearance. A matching KTI will allow these to be located on the approach
    chart.
    '''

    units = 'ft'

    def derive(self,
               alt_aal=P('Altitude AAL'),
               alt_tail=P('Altitude Tail'),
               dtl=P('Distance To Landing')):

        for desc_slice in alt_aal.slices_from_to(3000, 100):
            angle_array = alt_tail.array[desc_slice] \
                / (dtl.array[desc_slice] * FEET_PER_NM)
            index, value = min_value(angle_array)
            if index:
                sample = index + desc_slice.start
                self.create_kpv(sample, alt_tail.array[sample])


##############################################################################
# Terrain Clearance


class TerrainClearanceAbove3000FtMin(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Controlled Flight Into Terrain (CFIT) At/Below Minimum terrain clearance
    on approach/departure >3000ft AFE and <1000ft AGL"

    Solution: Compute minimum terrain clearance while Altitude AAL over 3000ft.
    Note: For most flights, Altitude Radio will be over 2,500ft at this time,
    so masked, hence no kpv will be created.
    '''

    units = 'ft'

    def derive(self,
               alt_rad=P('Altitude Radio'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            alt_rad.array,
            alt_aal.slices_above(3000),
            min_value,
        )


##############################################################################
# Tailwind


class TailwindLiftoffTo100FtMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Tailwind - Needs to be recorded just
    after take-off.

    CAA comment: Some operators will have purchased (AFM) a 15kt tailwind limit
    for take-off. But this should only be altered to 15 kt if it has been
    purchased.

    This event masks the tailwind array so that headwind conditions do not
    raise any KPV.
    '''

    def derive(self,
               tailwind=P('Tailwind'),
               alt_aal=P('Altitude AAL For Flight Phases')):

        self.create_kpvs_within_slices(
            np.ma.masked_less_equal(tailwind.array, 0.0),
            alt_aal.slices_from_to(0, 100),
            max_value,
        )


class Tailwind100FtToTouchdownMax(KeyPointValueNode):
    '''
    This event uses a masked tailwind array so that headwind conditions do
    not raise any KPV.
    '''

    def derive(self,
               tailwind=P('Tailwind'),
               alt_aal=P('Altitude AAL For Flight Phases'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_within_slices(
            np.ma.masked_less_equal(tailwind.array, 0.0),
            alt_aal.slices_to_kti(100, touchdowns),
            max_value,
        )


##############################################################################
# Warnings: Master Caution/Warning


class MasterWarningDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Master Caution or Master Warning
    triggered during takeoff. The idea of this is to inform the analyst of
    any possible distractions to the pilot"
    '''

    units = 's'

    def derive(self, warning=M('Master Warning')):
        self.create_kpvs_where(warning.array == 'Warning', warning.hz)


class MasterWarningDuringTakeoffDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Master Caution or Master Warning
    triggered during takeoff. The idea of this is to inform the analyst of
    any possible distractions to the pilot"
    '''

    units = 's'

    def derive(self, warning=M('Master Warning'),
               takeoff_rolls=S('Takeoff Roll')):
        self.create_kpvs_where(warning.array == 'Warning', 
                               warning.hz, phase=takeoff_rolls)


class MasterCautionDuringTakeoffDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Master Warning In Takeoff Duration".
    '''

    units = 's'

    def derive(self, caution=M('Master Caution'),
               takeoff_rolls=S('Takeoff Roll')):
        self.create_kpvs_where(caution.array == 'Caution',
                               caution.hz, phase=takeoff_rolls)


class PitchAlternateLawDuration(KeyPointValueNode):
    '''
    TODO: Review
    '''

    units = 's'

    def derive(self, alt_law=M('Pitch Alternate Law')):
        self.create_kpvs_where(alt_law.array == 'Alternate',
                               alt_law.hz)


class PitchDirectLawDuration(KeyPointValueNode):
    '''
    TODO: Review
    '''

    units = 's'

    def derive(self, dir_law=M('Pitch Direct Law')):
        self.create_kpvs_where(dir_law.array == 'Direct',
                               dir_law.hz)


##############################################################################
# Taxi In


class TaxiInDuration(KeyPointValueNode):
    '''
    '''
    
    units = '%'

    def derive(self,
               taxi_ins=S('Taxi In')):
        for taxi_in in taxi_ins:
            self.create_kpv(slice_midpoint(taxi_in.slice),
                            slice_duration(taxi_in.slice, self.frequency))


##############################################################################
# Taxi Out


class TaxiOutDuration(KeyPointValueNode):
    '''
    '''
    
    units = '%'

    def derive(self,
               taxi_outs=S('Taxi Out')):
        for taxi_out in taxi_outs:
            self.create_kpv(slice_midpoint(taxi_out.slice),
                            slice_duration(taxi_out.slice, self.frequency))


##############################################################################
# Warnings: Terrain Awareness & Warning System (TAWS)


class TAWSAlertDuration(KeyPointValueNode):
    '''
    The Duration to which the unspecified TAWS Alert is available.
    '''

    name = 'TAWS Alert Duration'
    units = 's'

    def derive(self, taws_alert=M('TAWS Alert'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_alert.array == 'Alert',
                               taws_alert.hz, phase=airborne)


class TAWSWarningDuration(KeyPointValueNode):
    '''
    The Duration to which the unspecified TAWS Warning is available.
    '''

    name = 'TAWS Warning Duration'
    units = 's'

    def derive(self, taws_warning=M('TAWS Warning'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_warning.array == 'Warning',
                               taws_warning.hz, phase=airborne)


class TAWSGeneralWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS General Warning Duration'
    units = 's'

    def derive(self, taws_general=M('TAWS General'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_general.array == 'Warning',
                               taws_general.hz, phase=airborne)


class TAWSSinkRateWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Sink Rate Warning Duration'
    units = 's'

    def derive(self, taws_sink_rate=M('TAWS Sink Rate'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_sink_rate.array == 'Warning',
                               taws_sink_rate.hz, phase=airborne)


class TAWSTooLowFlapWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Too Low Flap Warning Duration'
    units = 's'

    def derive(self, taws_too_low_flap=M('TAWS Too Low Flap'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_too_low_flap.array == 'Warning',
                               taws_too_low_flap.hz, phase=airborne)


class TAWSTerrainWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Terrain Warning Duration'
    units = 's'

    def derive(self, taws_terrain=M('TAWS Terrain'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_terrain.array == 'Warning',
                               taws_terrain.hz, phase=airborne)


class TAWSTerrainPullUpWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Terrain Pull Up Warning Duration'
    units = 's'

    def derive(self, taws_terrain_pull_up=M('TAWS Terrain Pull Up'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_terrain_pull_up.array == 'Warning',
                               taws_terrain_pull_up.hz, phase=airborne)


class TAWSGlideslopeWarning1500To1000FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Glideslope Warning 1500 To 1000 Ft Duration'
    units = 's'

    def derive(self, taws_glideslope=M('TAWS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_glideslope.array == 'Warning',
                               taws_glideslope.hz,
                               phase=alt_aal.slices_from_to(1500, 1000))


class TAWSGlideslopeWarning1000To500FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Glideslope Warning 1000 To 500 Ft Duration'
    units = 's'

    def derive(self, taws_glideslope=M('TAWS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_glideslope.array == 'Warning',
                               taws_glideslope.hz,
                               phase=alt_aal.slices_from_to(1000, 500))


class TAWSGlideslopeWarning500To200FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Glideslope Warning 500 To 200 Ft Duration'
    units = 's'

    def derive(self,
               taws_glideslope=M('TAWS Glideslope'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_glideslope.array == 'Warning',
                               taws_glideslope.hz,
                               phase=alt_aal.slices_from_to(500, 200))


class TAWSTooLowTerrainWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Too Low Terrain Warning Duration'
    units = 's'

    def derive(self, taws_too_low_terrain=M('TAWS Too Low Terrain'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_too_low_terrain.array == 'Warning',
                               taws_too_low_terrain.hz, phase=airborne)


class TAWSTooLowGearWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Too Low Gear Warning Duration'
    units = 's'

    def derive(self, taws_too_low_gear=M('TAWS Too Low Gear'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_too_low_gear.array == 'Warning',
                               taws_too_low_gear.hz, phase=airborne)


class TAWSPullUpWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Pull Up Warning Duration'
    units = 's'

    def derive(self, taws_pull_up=M('TAWS Pull Up'), airborne=S('Airborne')):
        self.create_kpvs_where(taws_pull_up.array == 'Warning',
                               taws_pull_up.hz, phase=airborne)


class TAWSDontSinkWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Dont Sink Warning Duration'
    units = 's'

    def derive(self, taws_dont_sink=M('TAWS Dont Sink'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_dont_sink.array == 'Warning',
                               taws_dont_sink.hz, phase=airborne)


class TAWSCautionObstacleDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Caution Obstacle Duration'
    units = 's'

    def derive(self, taws_caution_obstacle=M('TAWS Caution Obstacle'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_caution_obstacle.array == 'Caution',
                               taws_caution_obstacle.hz, phase=airborne)


class TAWSCautionTerrainDuration(KeyPointValueNode):
    '''
    TAWS Caution Terrain is sourced from GPWS
    '''

    name = 'TAWS Caution Terrain Duration'
    units = 's'

    def derive(self, taws_caution_terrain=M('TAWS Caution Terrain'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_caution_terrain.array == 'Caution',
                               taws_caution_terrain.hz, phase=airborne)


class TAWSTerrainCautionDuration(KeyPointValueNode):
    '''
    TAWS Terrain Caution is sourced from EGPWS.
    '''

    name = 'TAWS Terrain Caution Duration'
    units = 's'

    def derive(self, taws_terrain_caution=M('TAWS Terrain Caution'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_terrain_caution.array == 'Caution',
                               taws_terrain_caution.hz, phase=airborne)


class TAWSFailureDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Failure Duration'
    units = 's'

    def derive(self, taws_failure=M('TAWS Failure'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_failure.array == 'Failed',
                               taws_failure.hz, phase=airborne)


class TAWSObstacleWarningDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Obstacle Warning Duration'
    units = 's'

    def derive(self, taws_obstacle_warning=M('TAWS Obstacle Warning'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_obstacle_warning.array == 'Warning',
                               taws_obstacle_warning.hz, phase=airborne)


class TAWSPredictiveWindshearDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Predictive Windshear Duration'
    units = 's'

    def derive(self, taws_pw=M('TAWS Predictive Windshear'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_pw.array == 'Warning',
                               taws_pw.hz, phase=airborne)


class TAWSTerrainAheadDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Terrain Ahead Duration'
    units = 's'

    def derive(self, taws_terrain_ahead=M('TAWS Terrain Ahead'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_terrain_ahead.array == 'Warning',
                               taws_terrain_ahead.hz, phase=airborne)


class TAWSTerrainAheadPullUpDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Terrain Ahead Pull Up Duration'
    units = 's'

    def derive(self, taws_terrain_ahead_pu=M('TAWS Terrain Ahead Pull Up'),
               airborne=S('Airborne')):
        self.create_kpvs_where(taws_terrain_ahead_pu.array == 'Warning',
                               taws_terrain_ahead_pu.hz, phase=airborne)


class TAWSWindshearWarningBelow1500FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Windshear Warning Below 1500 Ft Duration'
    units = 's'

    def derive(self, taws_windshear=M('TAWS Windshear Warning'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_windshear.array == 'Warning',
                               taws_windshear.hz,
                               alt_aal.slices_from_to(1500, 0))


class TAWSWindshearCautionBelow1500FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Windshear Caution Below 1500 Ft Duration'
    units = 's'

    def derive(self, taws_windshear=M('TAWS Windshear Caution'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_windshear.array == 'Caution',
                               taws_windshear.hz,
                               alt_aal.slices_from_to(1500, 0))


class TAWSWindshearSirenBelow1500FtDuration(KeyPointValueNode):
    '''
    '''

    name = 'TAWS Windshear Siren Below 1500 Ft Duration'
    units = 's'

    def derive(self, taws_windshear=M('TAWS Windshear Siren'),
               alt_aal=P('Altitude AAL For Flight Phases')):
        self.create_kpvs_where(taws_windshear.array == 'Siren',
                               taws_windshear.hz,
                               alt_aal.slices_from_to(1500, 0))


##############################################################################
# Warnings: Traffic Collision Avoidance System (TCAS)


class TCASTAWarningDuration(KeyPointValueNode):
    '''
    This is simply the number of seconds during which the TCAS TA was set.
    '''

    name = 'TCAS TA Warning Duration'
    units = 's'

    def derive(self, tcas=M('TCAS Combined Control'), airs=S('Airborne')):
        
        for air in airs:
            ras_local = tcas.array[air.slice] == 'Preventive'
            ras_slices = shift_slices(runs_of_ones(ras_local), air.slice.start)
            self.create_kpvs_from_slice_durations(ras_slices, self.frequency,
                                                  mark='start')


class TCASRAWarningDuration(KeyPointValueNode):
    '''
    This is simply the number of seconds during which the TCAS RA was set.
    '''

    name = 'TCAS RA Warning Duration'
    units = 's'

    def derive(self, tcas=M('TCAS Combined Control'), airs=S('Airborne')):
        
        for air in airs:
            ras_local = tcas.array[air.slice].any_of('Drop Track',
                                                     'Altitude Lost',
                                                     'Up Advisory Corrective',
                                                     'Down Advisory Corrective',
                                                     ignore_missing=True)
            
            ras_slices = shift_slices(runs_of_ones(ras_local), air.slice.start)
            self.create_kpvs_from_slice_durations(ras_slices, self.frequency,
                                                  mark='start')


class TCASRAReactionDelay(KeyPointValueNode):
    '''
    This measures the time taken for the pilot to react, determined by the onset
    of the first major change in normal acceleration after the RA started.
    '''

    name = 'TCAS RA Reaction Delay'
    units = 's'

    def derive(self, acc=P('Acceleration Normal Offset Removed'),
               tcas=M('TCAS Combined Control'), airs=S('Airborne')):
        acc_array = repair_mask(acc.array, repair_duration=None)
        for air in airs:
            ras_local = tcas.array[air.slice].any_of('Drop Track',
                                                     'Altitude Lost',
                                                     'Up Advisory Corrective',
                                                     'Down Advisory Corrective',
                                                     ignore_missing=True)
            ras = shift_slices(runs_of_ones(ras_local), air.slice.start)
            # Assume that the reaction takes place during the TCAS RA period:
            for ra in ras:
                if np.ma.count(acc_array[ra]) == 0:
                    continue
                i, p = cycle_finder(acc_array[ra] - 1.0, 0.15)
                # i, p will be None if the data is too short or invalid and so
                # no cycles can be found.
                if i is None:
                    continue
                indexes = np.array(i)
                peaks = np.array(p)
                # Look beyond 2 seconds to find slope from point of initiation.
                slopes = np.ma.where(indexes > 17, abs(peaks / indexes), 0.0)
                start_to_peak = slice(ra.start, ra.start + i[np.argmax(slopes)])
                react_index = peak_curvature(acc_array, _slice=start_to_peak,
                                             curve_sense='Bipolar') - ra.start
                self.create_kpv(ra.start + react_index,
                                react_index / acc.frequency)


class TCASRAInitialReactionStrength(KeyPointValueNode):
    '''
    This measures the strength of the first reaction to the RA, in g per second.
    Most importantly, this is positive if the reaction is in the same sense as
    the Resolution Advisory (up for up or down for down) but negative in sign if
    the action is in the opposite direction to the RA.

    This is an ideal parameter for raising safety events when the pilot took
    the wrong initial action.
    '''

    name = 'TCAS RA Initial Reaction Strength'

    def derive(self, acc=P('Acceleration Normal Offset Removed'),
               tcas=M('TCAS Combined Control'), airs=S('Airborne')):
        
        for air in airs:
            ras_local = tcas.array[air.slice].any_of('Drop Track',
                                                     'Altitude Lost',
                                                     'Up Advisory Corrective',
                                                     'Down Advisory Corrective',
                                                     ignore_missing=True)
            ras = shift_slices(runs_of_ones(ras_local), air.slice.start)
            # We assume that the reaction takes place during the TCAS RA
            # period.
            for ra in ras:
                if np.ma.count(acc.array[ra]) == 0:
                    continue
                i, p = cycle_finder(acc.array[ra] - 1.0, 0.1)
                if i is None:
                    continue
                # Convert to Numpy arrays for ease of arithmetic
                indexes = np.array(i)
                peaks = np.array(p)
                slopes = np.ma.where(indexes > 17, abs(peaks / indexes), 0.0)
                s_max = np.argmax(slopes)

                # So we look for the steepest slope to the peak, which
                # ignores little early peaks or slightly high later peaks.
                # From inspection of many traces, this is the best way to
                # distinguish the peak of interest.
                if s_max == 0:
                    slope = peaks[0] / indexes[0]
                else:
                    slope = (peaks[s_max] - peaks[s_max - 1]) / \
                        (indexes[s_max] - indexes[s_max - 1])
                # Units of g/sec:
                slope *= acc.frequency

                if tcas.array[ra.start] == 5:
                    # Down advisory, so negative is good.
                    slope = -slope
                self.create_kpv(ra.start, slope)


class TCASRAToAPDisengagedDuration(KeyPointValueNode):
    '''
    Here we calculate the time between the onset of the RA and disconnection of
    the autopilot.

    Since the pilot's initial action should be to disengage the autopilot,
    this duration is another indication of pilot reaction time.
    '''

    name = 'TCAS RA To AP Disengaged Duration'
    units = 's'

    def derive(self,
               ap_offs=KTI('AP Disengaged Selection'),
               tcas=M('TCAS Combined Control'),
               airs=S('Airborne')):

        for air in airs:
            ras_local = tcas.array[air.slice].any_of('Drop Track',
                                                     'Altitude Lost',
                                                     'Up Advisory Corrective',
                                                     'Down Advisory Corrective',
                                                     ignore_missing=True)
            ras = shift_slices(runs_of_ones(ras_local), air.slice.start)
            # Assume that the reaction takes place during the TCAS RA period:
            for ra in ras:
                ap_off = ap_offs.get_next(ra.start, within_slice=ra)
                if not ap_off:
                    continue
                index = ap_off.index
                duration = (index - ra.start) / self.frequency
                self.create_kpv(index, duration)


class TCASFailureDuration(KeyPointValueNode):
    '''
    '''

    name = 'TCAS Failure Duration'
    units = 's'

    def derive(self, tcas_failure=M('TCAS Failure'),
               airborne=S('Airborne')):
        self.create_kpvs_where(tcas_failure.array == 'Failed',
                               tcas_failure.hz, phase=airborne)


##############################################################################
# Warnings: Takeoff Configuration


class TakeoffConfigurationWarningDuration(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take-Off (Longitudinal), Take-off config warning during
    takeoff roll."
    '''

    units = 's'
    
    def derive(self, takeoff_warn=M('Takeoff Configuration Warning'),
               takeoff=S('Takeoff Roll')):
        self.create_kpvs_where(takeoff_warn.array == 'Warning',
                               takeoff_warn.hz, phase=takeoff)


class TakeoffConfigurationFlapWarningDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self, takeoff_warn=M('Takeoff Configuration Flap Warning'),
               takeoff=S('Takeoff Roll')):
        self.create_kpvs_where(takeoff_warn.array == 'Warning',
                               takeoff_warn.hz, phase=takeoff)


class TakeoffConfigurationParkingBrakeWarningDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               takeoff_warn=M('Takeoff Configuration Parking Brake Warning'),
               takeoff=S('Takeoff Roll')):
        self.create_kpvs_where(takeoff_warn.array == 'Warning',
                               takeoff_warn.hz, phase=takeoff)


class TakeoffConfigurationSpoilerWarningDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               takeoff_cfg_warn=M('Takeoff Configuration Spoiler Warning'),
               takeoff=S('Takeoff Roll')):
        self.create_kpvs_where(takeoff_cfg_warn.array == 'Warning',
                               takeoff_cfg_warn.hz, phase=takeoff)


class TakeoffConfigurationStabilizerWarningDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               takeoff_cfg_warn=M('Takeoff Configuration Stabilizer Warning'),
               takeoff=S('Takeoff Roll')):
        self.create_kpvs_where(takeoff_cfg_warn.array == 'Warning',
                               takeoff_cfg_warn.hz, phase=takeoff)


##############################################################################
# Warnings: Takeoff Configuration


class LandingConfigurationGearWarningDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               landing_cfg_warn=M('Landing Configuration Gear Warning'),
               airs=S('Airborne')):
        self.create_kpvs_where(landing_cfg_warn.array == 'Warning',
                               landing_cfg_warn.hz, phase=airs)


class LandingConfigurationSpeedbrakeCautionDuration(KeyPointValueNode):
    '''
    '''

    units = 's'

    def derive(self,
               landing_cfg_caution=M(
                   'Landing Configuration Speedbrake Caution'),
               airs=S('Airborne')):
        self.create_kpvs_where(landing_cfg_caution.array == 'Caution',
                               landing_cfg_caution.hz, phase=airs)


##############################################################################
# Throttle


class ThrottleCyclesDuringFinalApproach(KeyPointValueNode):
    '''
    Counts the number of half-cycles of throttle lever movement that exceed
    10 deg peak to peak and with a maximum cycle period of 14 seconds during
    the final approach phase.
    '''

    units = 'cycles'

    def derive(self, levers=P('Throttle Levers'), 
               fin_apps=S('Final Approach')):

        for fin_app in fin_apps:
            self.create_kpv(*cycle_counter(
                levers.array[fin_app.slice],
                10.0, 10.0, levers.hz,
                fin_app.slice.start,
            ))


##############################################################################
# Thrust Asymmetry


class ThrustAsymmetryDuringTakeoffMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Take off (Lateral)" & "Loss of Control Significant torque
    or thrust split during T/O or G/A"
    '''

    units = '%'

    def derive(self, ta=P('Thrust Asymmetry'),
               takeoff_rolls=S('Takeoff Roll')):

        self.create_kpvs_within_slices(ta.array, takeoff_rolls, max_value)


class ThrustAsymmetryDuringFlightMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Asymmetric thrust - may be due to an a/t fault"
    '''

    units = '%'

    def derive(self, ta=P('Thrust Asymmetry'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(ta.array, airborne, max_value)


class ThrustAsymmetryDuringGoAroundMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Loss of Control Significant torque or thrust split during T/O or G/A"
    '''

    units = '%'

    def derive(self, ta=P('Thrust Asymmetry'),
               go_arounds=S('Go Around And Climbout')):

        self.create_kpvs_within_slices(ta.array, go_arounds, max_value)


class ThrustAsymmetryDuringApproachMax(KeyPointValueNode):
    '''
    Peak thrust asymmetry on approach. A good KPV for providing measures on
    every flight, and preferred to the ThrustAsymmetryOnApproachDuration
    which will normally not record any value.
    '''

    units = '%'

    def derive(self, ta=P('Thrust Asymmetry'),
               approaches=S('Approach')):

        self.create_kpvs_within_slices(ta.array, approaches, max_value)


class ThrustAsymmetryWithThrustReversersDeployedMax(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral) - Asymmetric reverse thrust".

    A good KPV for providing measures on every flight, and preferred to the
    ThrustAsymmetryWithReverseThrustDuration which will normally not record
    any value.
    '''

    units = '%'

    def derive(self, ta=P('Thrust Asymmetry'), tr=M('Thrust Reversers'),
               mobile=S('Mobile')):
        # Note: Inclusion of the 'Mobile' phase ensures use of thrust reverse
        #       late on the landing run is included, but corrupt data at engine
        #       start etc. should be rejected.
        # Note: Use not 'Stowed' as 'In Transit' implies partially 'Deployed':
        slices = clump_multistate(tr.array, 'Stowed', mobile.get_slices(),
                                  condition=False)
        self.create_kpvs_within_slices(ta.array, slices, max_value)


class ThrustAsymmetryDuringApproachDuration(KeyPointValueNode):
    '''
    Durations of thrust asymmetry over 10%. Included for customers with
    existing events using this approach.
    '''

    units = 's'

    def derive(self, ta=P('Thrust Asymmetry'), approaches=S('Approach')):
        for approach in approaches:
            asymmetry = np.ma.masked_less(ta.array[approach.slice], 10.0)
            slices = np.ma.clump_unmasked(asymmetry)
            slices = shift_slices(slices, approach.slice.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency)


class ThrustAsymmetryWithThrustReversersDeployedDuration(KeyPointValueNode):
    '''
    Durations of thrust asymmetry over 10% with reverse thrust operating.
    Included for customers with existing events using this approach.
    '''

    units = 's'

    def derive(self,
               ta=P('Thrust Asymmetry'),
               tr=M('Thrust Reversers'),
               mobile=S('Mobile')):

        # Note: Inclusion of the 'Mobile' phase ensures use of thrust reverse
        #       late on the landing run is included, but corrupt data at engine
        #       start etc. should be rejected.
        slices = [s.slice for s in mobile]
        # Note: Use not 'Stowed' as 'In Transit' implies partially 'Deployed':
        slices = clump_multistate(tr.array, 'Stowed', slices, condition=False)
        for slice_ in slices:
            asymmetry = np.ma.masked_less(ta.array[slice_], 10.0)
            slices = np.ma.clump_unmasked(asymmetry)
            slices = shift_slices(slices, slice_.start)
            self.create_kpvs_from_slice_durations(slices, self.frequency)


##############################################################################


class TouchdownToElevatorDownDuration(KeyPointValueNode):
    '''
    Originally introduced to monitor pilot actions on landing, the first
    version of this algorithm triggered only on -14deg elevator setting, to
    suit the Boeing 757 aircraft type.
    
    This was amended to monitor the time to maximum elevator down should the
    14 deg threshold not be met, in any case requiring at least 10 deg change
    in elevator to indicate a significant removal of lift.
    '''
    def derive(self, airspeed=P('Airspeed'), 
               elevator=P('Elevator'),
               tdwns=KTI('Touchdown'),
               lands=S('Landing')):
        
        for land in lands:
            for tdwn in tdwns:
                if not is_index_within_slice(tdwn.index, land.slice):
                    continue
                to_scan = slice(tdwn.index,land.slice.stop)
                
                index_elev = index_at_value(elevator.array, -14.0, to_scan )
                if index_elev:
                    t_14 = (index_elev - tdwn.index) / elevator.frequency
                    self.create_kpv(index_elev, t_14)
                    
                else:
                    index_min = tdwn.index+np.ma.argmin(elevator.array[to_scan])
                    if index_min > tdwn.index+2:
                        # Worth having a look
                        if np.ma.ptp(elevator.array[tdwn.index:index_min]) > 10.0:
                            t_min= (index_min - tdwn.index) / elevator.frequency
                            self.create_kpv(index_min, t_min)
                    else:
                        # Nothing useful to do.
                        pass

class TouchdownTo60KtsDuration(KeyPointValueNode):
    """
    Ideally compute using groundspeed, otherwise use airspeed.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and 'Touchdown' in available

    def derive(self, airspeed=P('Airspeed'), groundspeed=P('Groundspeed'),
               tdwns=KTI('Touchdown')):

        if groundspeed:
            speed=groundspeed.array
            freq=groundspeed.frequency
        else:
            speed=airspeed.array
            freq=airspeed.frequency

        for tdwn in tdwns:
            index_60kt = index_at_value(speed, 60.0, slice(tdwn.index,None))
            if index_60kt:
                t__60kt = (index_60kt - tdwn.index) / freq
                self.create_kpv(index_60kt, t__60kt)


##############################################################################
# Turbulence


class TurbulenceDuringApproachMax(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               turbulence=P('Turbulence RMS g'),
               approaches=S('Approach')):

        self.create_kpvs_within_slices(turbulence.array, approaches, max_value)


class TurbulenceDuringCruiseMax(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               turbulence=P('Turbulence RMS g'),
               cruises=S('Cruise')):

        self.create_kpvs_within_slices(turbulence.array, cruises, max_value)


class TurbulenceDuringFlightMax(KeyPointValueNode):
    '''
    '''

    units = 'g'

    def derive(self,
               turbulence=P('Turbulence RMS g'),
               airborne=S('Airborne')):

        self.create_kpvs_within_slices(turbulence.array, airborne, max_value)


##############################################################################
# Wind


class WindSpeedAtAltitudeDuringDescent(KeyPointValueNode):
    '''
    Note: We align to Altitude AAL for cosmetic reasons; alignment to wind
          speed leads to slightly misaligned KPVs for wind speed, which looks
          wrong although is arithmetically "correct".
    '''

    NAME_FORMAT = 'Wind Speed At %(altitude)d Ft During Descent'
    NAME_VALUES = {'altitude': [2000, 1500, 1000, 500, 100, 50]}
    units = 'kt'

    def derive(self,
               alt_aal=P('Altitude AAL For Flight Phases'),
               wind_spd=P('Wind Speed')):

        for descent in alt_aal.slices_from_to(2100, 0):
            for altitude in self.NAME_VALUES['altitude']:
                index = index_at_value(alt_aal.array, altitude, descent)
                if not index:
                    continue
                value = value_at_index(wind_spd.array, index)
                if value:
                    self.create_kpv(index, value, altitude=altitude)


class WindDirectionAtAltitudeDuringDescent(KeyPointValueNode):
    '''
    Note: We align to Altitude AAL for cosmetic reasons; alignment to wind
          direction leads to slightly misaligned KPVs for wind direction, which
          looks wrong although is arithmetically "correct".
    '''

    NAME_FORMAT = 'Wind Direction At %(altitude)d Ft During Descent'
    NAME_VALUES = {'altitude': [2000, 1500, 1000, 500, 100, 50]}
    units = 'kt'

    def derive(self,
               alt_aal=P('Altitude AAL For Flight Phases'),
               wind_dir=P('Wind Direction Continuous')):

        for descent in alt_aal.slices_from_to(2100, 0):
            for altitude in self.NAME_VALUES['altitude']:
                index = index_at_value(alt_aal.array, altitude, descent)
                if not index:
                    continue
                # Check direction not masked before using % 360:
                value = value_at_index(wind_dir.array, index)
                if value:
                    self.create_kpv(index, value % 360.0, altitude=altitude)


class WindAcrossLandingRunwayAt50Ft(KeyPointValueNode):
    '''
    FDS developed this KPV to support the UK CAA Significant Seven programme.
    "Excursions - Landing (Lateral). Crosswind - needs to be recorded just
    before landing, say at 50ft.
    '''

    units = 'kt'

    def derive(self,
               walr=P('Wind Across Landing Runway'),
               landings=S('Landing')):
        for landing in landings:
            index = landing.slice.start  # Landing starts at 50ft!
            value = walr.array[index]
            if value is not None:
                self.create_kpv(index, value)


##############################################################################
# Weight


class GrossWeightAtLiftoff(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               gross_wgt=P('Gross Weight Smoothed'),
               liftoffs=KTI('Liftoff')):

        self.create_kpvs_at_ktis(
            repair_mask(gross_wgt.array, repair_duration=None), liftoffs)


class GrossWeightAtTouchdown(KeyPointValueNode):
    '''
    '''

    units = 'kg'

    def derive(self,
               gross_wgt=P('Gross Weight Smoothed'),
               touchdowns=KTI('Touchdown')):

        self.create_kpvs_at_ktis(
            repair_mask(gross_wgt.array, repair_duration=None), touchdowns)


class ZeroFuelWeight(KeyPointValueNode):
    '''
    The aircraft zero fuel weight is computed from the recorded gross weight
    and fuel data.

    See also the GrossWeightSmoothed calculation which uses fuel flow data to
    obtain a higher sample rate solution to the aircraft weight calculation,
    with a best fit to the available weight data.
    '''

    units = 'kg'

    def derive(self,
               fuel_qty=P('Fuel Qty'),
               gross_wgt=P('Gross Weight')):

        self.create_kpv(0, np.ma.median(gross_wgt.array - fuel_qty.array))


class GrossWeightDelta60SecondsInFlightMax(KeyPointValueNode):
    """
    Measure the maximum change of gross weight over a one minute window. This is
    primarily to detect manual adjustments of the aircraft gross wieght during
    the flight by the crew. In order to capture lots of little adjustments
    together, a one minute window is used.
    """
    units = 'kg'
    align_frequency = 1  #force to 1Hz for 60 second measurements
    
    def derive(self, gross_weight=P('Gross Weight'), airborne=S('Airborne')):
        # use the recorded un-smoothed gross weight measurements
        gw_repaired = repair_mask(gross_weight.array)
        weight_diff = np.ma.ediff1d(gw_repaired[::60])
        for in_air in airborne:
            # working in 60th samples
            in_air_start = (in_air.slice.start / 60.0) if in_air.slice.start else None
            in_air_stop = (in_air.slice.stop / 60.0) if in_air.slice.stop else None
            in_air_slice = slice(in_air_start, in_air_stop)
            max_diff = max_abs_value(weight_diff, _slice=in_air_slice)
            # narrow down the index to the maximum change in this region of flight
            max_diff_slice = slice(max_diff.index * 60,
                                   (max_diff.index + 1) * 60)
            index, _ = max_abs_value(gw_repaired, _slice=max_diff_slice)
            self.create_kpv(index, max_diff.value)


##############################################################################
# Dual Input


class DualInputDuration(KeyPointValueNode):
    '''
    Duration of dual input.
    '''
    unit = 's'

    def derive(self, dual=M('Dual Input Warning')):

        self.create_kpvs_where(dual.array == 'Dual', dual.hz)


class DualInputByCaptDuration(KeyPointValueNode):
    '''
    Duration of dual input by the captain with first officer flying.
    '''
    unit = 's'

    def derive(self,
               dual=M('Dual Input Warning'),
               pilot=M('Pilot Flying')):

        condition = (dual.array == 'Dual') & (pilot.array == 'FO')
        self.create_kpvs_where(condition, dual.hz)


class DualInputByFODuration(KeyPointValueNode):
    '''
    Duration of dual input by the first officer with captain flying.
    '''
    name = 'Dual Input By FO Duration'
    unit = 's'

    def derive(self,
               dual=M('Dual Input Warning'),
               pilot=M('Pilot Flying')):

        condition = (dual.array == 'Dual') & (pilot.array == 'Capt')
        self.create_kpvs_where(condition, dual.hz)


##############################################################################


class HoldingDuration(KeyPointValueNode):
    """
    Identify time spent in the hold.
    """
    def derive(self, holds=S('Holding')):
        self.create_kpvs_from_slice_durations(holds, self.frequency, mark='end')


##### TODO: Implement!
####class ControlForcesTimesThree(KeyPointValueNode):
####    def derive(self, x=P('Not Yet')):
####        return NotImplemented

##############################################################################


# NOTE: Python class name restriction: '2 Deg Pitch To 35 Ft Duration'
class TwoDegPitchTo35FtDuration(KeyPointValueNode):
    '''
    Time taken for aircraft to reach 35ft after rotating to 2 degrees pitch.
    '''

    name = '2 Deg Pitch To 35 Ft Duration'
    units = 's'

    def derive(self, two_deg_pitch_to_35ft=S('2 Deg Pitch To 35 Ft')):

        self.create_kpvs_from_slice_durations(
            two_deg_pitch_to_35ft,
            self.frequency,
            mark='midpoint',
        )


class LastFlapChangeToTakeoffRollEndDuration(KeyPointValueNode):
    '''
    Time between the last flap change during takeoff roll and the end of
    takeoff roll.

    The idea is that the flaps should not be changed when the aircraft has
    started accelerating down the runway.

    We detect the last change of flap selection during the takeoff roll phase
    and calculate the time between this instant and the end of takeoff roll.
    '''
    units = 's'
    
    @classmethod
    def can_operate(cls, available):
        return (('Flap Lever' in available or 'Flap' in available) and
                'Takeoff Roll' in available)

    def derive(self, flap_lever=M('Flap Lever'),
               flap_synth=M('Flap'),
               rolls=S('Takeoff Roll')):
        flap = flap_lever or flap_synth
        
        for roll in rolls:
            changes = find_edges(flap.array.raw, roll.slice, 'all_edges')
            if changes:
                roll_end = roll.slice.stop
                last_change = changes[-1]
                time_from_liftoff = (roll_end - last_change) / self.frequency
                self.create_kpv(last_change, time_from_liftoff)


class AirspeedMinusVMOMax(KeyPointValueNode):
    '''
    Maximum VMO exceeding.
    '''

    name = 'Airspeed Minus VMO Max'
    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        return any_of(('VMO', 'VMO Lookup'), available) \
            and 'Airspeed' in available\
            and 'Airborne' in available

    def derive(self, airspeed=P('Airspeed'), vmo=P('VMO'),
               vmol=P('VMO Lookup'), airborne=S('Airborne')):
        if not vmo and vmol:
            vmo = vmol

        exceedings = airspeed.array - vmo.array
        self.create_kpvs_within_slices(
            exceedings,
            airborne,
            max_value,
        )


class MachMinusMMOMax(KeyPointValueNode):
    '''
    Maximum MMO exceeding.
    '''

    name = 'Mach Minus MMO Max'
    units = 'M'

    @classmethod
    def can_operate(cls, available):
        return any_of(('MMO', 'MMO Lookup'), available) \
            and 'Mach' in available\
            and 'Airborne' in available

    def derive(self, mach=P('Mach'), mmo=P('MMO'), mmol=P('MMO Lookup'),
               airborne=S('Airborne')):
        if not mmo and mmol:
            mmo = mmol

        exceedings = mach.array - mmo.array
        self.create_kpvs_within_slices(
            exceedings,
            airborne,
            max_value,
        )
