# -*- coding: utf-8 -*-

import math
import logging

import numpy as np

from flightdatautilities.model_information import (
    get_aileron_map,
    get_conf_map,
    get_flap_map,
    get_flap_values_mapping,
    get_slat_map,
)

from hdfaccess.parameter import MappedArray

#from analysis_engine.exceptions import DataFrameError
from analysis_engine.node import (
    A, MultistateDerivedParameterNode, 
    #KPV, KTI, 
    M,
    P,
    S
)
from analysis_engine.library import (#actuator_mismatch,
                                     #air_track,
                                     #align,
                                     all_of,
                                     any_of,
                                     #alt2press,
                                     #alt2sat,
                                     #bearing_and_distance,
                                     #bearings_and_distances,
                                     #blend_parameters,
                                     #blend_two_parameters,
                                     #cas2dp,
                                     #coreg,
                                     #cycle_finder,
                                     datetime_of_index,
                                     #dp2tas,
                                     #dp_over_p2mach,
                                     #filter_vor_ils_frequencies,
                                     find_edges_on_state_change,
                                     #first_valid_sample,
                                     #first_order_lag,
                                     #first_order_washout,
                                     #ground_track,
                                     #ground_track_precise,
                                     #hysteresis,
                                     index_at_value,
                                     #integrate,
                                     #ils_localizer_align,
                                     index_closest_value,
                                     #interpolate,
                                     is_day,
                                     #is_index_within_slice,
                                     #last_valid_sample,
                                     #latitudes_and_longitudes,
                                     #localizer_scale,
                                     #machtat2sat,
                                     #mask_inside_slices,
                                     #mask_outside_slices,
                                     #max_value,
                                     merge_masks,
                                     merge_sources,
                                     merge_two_parameters,
                                     moving_average,
                                     #np_ma_ones_like,
                                     np_ma_masked_zeros_like,
                                     np_ma_zeros_like,
                                     offset_select,
                                     #peak_curvature,
                                     #rate_of_change,
                                     repair_mask,
                                     #rms_noise,
                                     round_to_nearest,
                                     runs_of_ones,
                                     #runway_deviation,
                                     #runway_distances,
                                     #runway_heading,
                                     #runway_length,
                                     #runway_snap_dict,
                                     #shift_slice,
                                     #slices_between,
                                     slices_from_to,
                                     #slices_not,
                                     #slices_or,
                                     slices_remove_small_gaps,
                                     slices_remove_small_slices,
                                     #smooth_track,
                                     step_values,
                                     #straighten_altitudes,
                                     #straighten_headings,
                                     #second_window,
                                     #track_linking,
                                     #value_at_index,
                                     vstack_params,
                                     vstack_params_where_state,
                                     )

#from settings import (AZ_WASHOUT_TC,
                      #FEET_PER_NM,
                      #HYSTERESIS_FPIAS,
                      #HYSTERESIS_FPROC,
                      #GRAVITY_IMPERIAL,
                      #KTS_TO_FPS,
                      #KTS_TO_MPS,
                      #METRES_TO_FEET,
                      #METRES_TO_NM,
                      #VERTICAL_SPEED_LAG_TC)


logger = logging.getLogger(name=__name__)


class APEngaged(MultistateDerivedParameterNode):
    '''
    Determines if *any* of the "AP (*) Engaged" parameters are recording the
    state of Engaged.
    
    This is a discrete with only the Engaged state.
    '''

    name = 'AP Engaged'
    align = False  #TODO: Should this be here?
    values_mapping = {0: '-', 1: 'Engaged'}

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, ap1=M('AP (1) Engaged'),
                     ap2=M('AP (2) Engaged'),
                     ap3=M('AP (3) Engaged')):
        stacked = vstack_params_where_state(
            (ap1, 'Engaged'),
            (ap2, 'Engaged'),
            (ap3, 'Engaged'),
            )
        self.array = stacked.any(axis=0)
        if ap1:
            self.frequency = ap1.frequency
        elif ap2:
            self.frequency = ap2.frequency
        else:
            self.frequency = ap3.frequency
        self.offset = offset_select('mean', [ap1, ap2, ap3])


class APChannelsEngaged(MultistateDerivedParameterNode):
    '''
    Assess the number of autopilot systems engaged.

    Airbus and Boeing = 1 autopilot at a time except when "Land" mode
    selected when 2 (Dual) or 3 (Triple) can be engaged. Airbus favours only
    2 APs, Boeing is happier with 3 though some older types may only have 2.
    '''
    name = 'AP Channels Engaged'
    align = False  # TODO: Should this be here?
    values_mapping = {0: '-', 1: 'Single', 2: 'Dual', 3: 'Triple'}

    @classmethod
    def can_operate(cls, available):
        return len(available) >= 2

    def derive(self,
               ap1=M('AP (1) Engaged'),
               ap2=M('AP (2) Engaged'),
               ap3=M('AP (3) Engaged')):
        stacked = vstack_params_where_state(
            (ap1, 'Engaged'),
            (ap2, 'Engaged'),
            (ap3, 'Engaged'),
        )
        self.array = stacked.sum(axis=0)
        # Assume all are sampled at the same frequency
        self.frequency = ap1.frequency
        self.offset = offset_select('mean', [ap1, ap2, ap3])


class APURunning(MultistateDerivedParameterNode):
    '''
    Simple measure of APU status, suitable for plotting if you want an on/off
    measure. Used for fuel usage measurements.
    '''

    values_mapping = {0 : '-',  1 : 'Running'}

    @classmethod
    def can_operate(cls, available):
        return 'APU N1' in available
    
    def derive(self, apu_n1=P('APU N1')):
        self.array = np.ma.where(apu_n1.array > 50.0, 'Running', '-')
    
    
class Configuration(MultistateDerivedParameterNode):
    '''
    Parameter for aircraft that use configuration.

    Multi-state with the following mapping::

        {
            0 : '0',
            1 : '1',
            2 : '1+F',
            3 : '1*',
            4 : '2',
            5 : '2*',
            6 : '3',
            7 : '4',
            8 : '5',
            9 : 'Full',
        }

    Some values are based on footnotes in various pieces of documentation:

    - 2(a) corresponds to CONF 1*
    - 3(b) corresponds to CONF 2*

    Note: Does not use the Flap Lever position. This parameter reflects the
    actual configuration state of the aircraft rather than the intended state
    represented by the selected lever position.

    Note: Values that do not map directly to a required state are masked with
    the data being random (memory alocated)
    '''

    values_mapping = {
        0 : '0',
        1 : '1',
        2 : '1+F',
        3 : '1*',
        4 : '2',
        5 : '2*',
        6 : '3',
        7 : '4',
        8 : '5',
        9 : 'Full',
    }

    @classmethod
    def can_operate(cls, available, manu=A('Manufacturer')):
        if manu and manu.value != 'Airbus':
            return False
        return all_of(('Slat', 'Flap', 'Series', 'Family'), available)

    def derive(self, slat=P('Slat'), flap=M('Flap'), flaperon=P('Flaperon'),
               series=A('Series'), family=A('Family')):

        mapping = get_conf_map(series.value, family.value)
        qty_param = len(mapping.itervalues().next())
        if qty_param == 3 and not flaperon:
            # potential problem here!
            self.warning("Flaperon not available, so will calculate "
                         "Configuration using only slat and flap")
            qty_param = 2
        elif qty_param == 2 and flaperon:
            # only two items in values tuple
            self.debug("Flaperon available but not required for "
                       "Configuration calculation")
            pass

        #TODO: Scale each parameter individually to ensure uniqueness.
        
        # Sum the required parameters (creates a unique state value at present)
        summed = vstack_params(*(slat, flap, flaperon)[:qty_param]).sum(axis=0)

        # create a placeholder array fully masked
        self.array = MappedArray(np_ma_masked_zeros_like(flap.array), 
                                 self.values_mapping)
        for state, values in mapping.iteritems():
            s = np.ma.sum(values[:qty_param])
            # unmask bits we know about
            self.array[summed == s] = state


class Daylight(MultistateDerivedParameterNode):
    '''
    Calculate Day or Night based upon Civil Twilight.
    
    FAA Regulation FAR 1.1 defines night as: "Night means the time between
    the end of evening civil twilight and the beginning of morning civil
    twilight, as published in the American Air Almanac, converted to local
    time.

    EASA EU OPS 1 Annex 1 item (76) states: 'night' means the period between
    the end of evening civil twilight and the beginning of morning civil
    twilight or such other period between sunset and sunrise as may be
    prescribed by the appropriate authority, as defined by the Member State;

    CAA regulations confusingly define night as 30 minutes either side of
    sunset and sunrise, then include a civil twilight table in the AIP.

    With these references, it was decided to make civil twilight the default.
    '''
    align = True
    align_frequency = 0.25
    align_offset = 0.0

    values_mapping = {
        0 : 'Night',
        1 : 'Day'
        }

    def derive(self,
               latitude=P('Latitude Smoothed'),
               longitude=P('Longitude Smoothed'),
               start_datetime=A('Start Datetime'),
               duration=A('HDF Duration')):
        # Set default to 'Day'
        array_len = duration.value * self.frequency
        self.array = np.ma.ones(array_len)
        for step in xrange(int(array_len)):
            curr_dt = datetime_of_index(start_datetime.value, step, 1)
            lat = latitude.array[step]
            lon = longitude.array[step]
            if lat and lon:
                if not is_day(curr_dt, lat, lon):
                    # Replace values with Night
                    self.array[step] = 0
                else:
                    continue  # leave array as 1
            else:
                # either is masked or recording 0.0 which is invalid too
                self.array[step] = np.ma.masked


class DualInputWarning(MultistateDerivedParameterNode):
    '''
    '''
    values_mapping = {0: '-', 1: 'Dual'}

    def derive(self,
               pilot=M('Pilot Flying'),
               stick_capt=P('Sidestick Angle (Capt)'),
               stick_fo=P('Sidestick Angle (FO)')):

        array = np_ma_zeros_like(pilot.array)
        array[pilot.array == 'Capt'] = stick_fo.array[pilot.array == 'Capt']
        array[pilot.array == 'FO'] = stick_capt.array[pilot.array == 'FO']
        array = np.ma.array(array > 0.5, mask=array.mask, dtype=int)

        slices = runs_of_ones(array)
        slices = slices_remove_small_slices(slices, 3, self.hz)
        slices = slices_remove_small_gaps(slices, 15, self.hz)

        dual = np_ma_zeros_like(array)
        for sl in slices:
            dual[sl] = 1
        self.array = dual


class Eng_1_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (1) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (1) Fire On Ground'),
               fire_air=M('Eng (1) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_2_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (2) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (2) Fire On Ground'),
               fire_air=M('Eng (2) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_3_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (3) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (3) Fire On Ground'),
               fire_air=M('Eng (3) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_4_Fire(MultistateDerivedParameterNode):
    '''
    Combine on ground and in air fire warnings.
    '''

    name = 'Eng (4) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    def derive(self,
               fire_gnd=M('Eng (4) Fire On Ground'),
               fire_air=M('Eng (4) Fire In Air')):

        self.array = vstack_params_where_state(
            (fire_gnd, 'Fire'),
            (fire_air, 'Fire'),
        ).any(axis=0)


class Eng_Fire(MultistateDerivedParameterNode):
    '''
    Merges all the engine fire signals into one.
    '''
    name = 'Eng (*) Fire'
    values_mapping = {0: '-', 1: 'Fire'}

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=M('Eng (1) Fire'),
               eng2=M('Eng (2) Fire'),
               eng3=M('Eng (3) Fire'),
               eng4=M('Eng (4) Fire'),
               eng1_1l=M('Eng (1) Fire (1L)'),
               eng1_1r=M('Eng (1) Fire (1R)'),
               eng1_2l=M('Eng (1) Fire (2L)'),
               eng1_2r=M('Eng (1) Fire (2R)'),
               ):

        self.array = vstack_params_where_state(
            (eng1, 'Fire'), (eng2, 'Fire'),
            (eng3, 'Fire'), (eng4, 'Fire'),
            (eng1_1l, 'Fire'), (eng1_1r, 'Fire'),
            (eng1_2l, 'Fire'), (eng1_2r, 'Fire'),
        ).any(axis=0)


class Eng_AllRunning(MultistateDerivedParameterNode):
    '''
    Discrete parameter describing when all available engines are running.
    
    TODO: Include Fuel cut-off switch if recorded?
    
    TODO: Confirm that all engines were recording for the N2 Min / Fuel Flow
    Min parameters - theoretically there could be only three engines in the
    frame for a four engine aircraft. Use "Engine Count".
    
    TODO: Support shutdown for Propellor aircraft that don't record fuel flow.
    '''
    name = 'Eng (*) All Running'
    values_mapping = {
        0 : 'Not Running',
        1 : 'Running',
        }
    
    @classmethod
    def can_operate(cls, available):
        return 'Eng (*) N1 Min' in available or \
               'Eng (*) N2 Min' in available or \
               'Eng (*) Fuel Flow Min' in available
    
    def derive(self,
               eng_n1=P('Eng (*) N1 Min'),
               eng_n2=P('Eng (*) N2 Min'),
               fuel_flow=P('Eng (*) Fuel Flow Min')):
        # TODO: move values to settings

        if eng_n2 or fuel_flow:
            # Ideally have N2 and Fuel Flow with both available,
            # otherwise use just one source
            n2_running = eng_n2.array > 10 if eng_n2 \
                else np.ones_like(fuel_flow.array, dtype=bool)
            fuel_flowing = fuel_flow.array > 50 if fuel_flow \
                else np.ones_like(eng_n2.array, dtype=bool)
            self.array = n2_running & fuel_flowing
        else:
            # Fall back on N1 
            self.array = eng_n1.array > 10
            # TODO: extend to NP for props


class Eng_AnyRunning(MultistateDerivedParameterNode):
    '''
    Discrete parameter describing when any engines are running.

    This is useful with 'Eng (*) All Running' to detect if not all engines are
    running.
    '''
    name = 'Eng (*) Any Running'
    values_mapping = {
        0: 'Not Running',
        1: 'Running',
    }

    @classmethod
    def can_operate(cls, available):
        return 'Eng (*) N1 Max' in available or \
               'Eng (*) N2 Max' in available or \
               'Eng (*) Fuel Flow Max' in available

    def derive(self,
               eng_n1=P('Eng (*) N1 Max'),
               eng_n2=P('Eng (*) N2 Max'),
               fuel_flow=P('Eng (*) Fuel Flow Max')):

        if eng_n2 or fuel_flow:
            # TODO: move values to settings
            n2_running = eng_n2.array > 10 if eng_n2 \
                else np.ones_like(fuel_flow.array, dtype=bool)
            fuel_flowing = fuel_flow.array > 50 if fuel_flow \
                else np.ones_like(eng_n2.array, dtype=bool)
            # must have N2 and Fuel Flow if both are available
            self.array = n2_running & fuel_flowing
        else:
            # Only have N1 available
            self.array = eng_n1.array > 10
            # TODO: extend to NP for props


class EngThrustModeRequired(MultistateDerivedParameterNode):
    '''
    Combines Eng Thrust Mode Required parameters.
    '''
    
    values_mapping = {
        0: '-',
        1: 'Required',
    }
    
    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)
    
    def derive(self,
               thrust1=P('Eng (1) Thrust Mode Required'),
               thrust2=P('Eng (2) Thrust Mode Required'),
               thrust3=P('Eng (3) Thrust Mode Required'),
               thrust4=P('Eng (4) Thrust Mode Required')):
        
        thrusts = [thrust for thrust in [thrust1,
                                         thrust2,
                                         thrust3,
                                         thrust4] if thrust]
        
        if len(thrusts) == 1:
            self.array = thrusts[0].array
        
        array = MappedArray(np_ma_zeros_like(thrusts[0].array),
                            values_mapping=self.values_mapping)
        
        masks = []
        for thrust in thrusts:
            masks.append(thrust.array.mask)
            array[thrust.array == 'Required'] = 'Required'
        
        array.mask = merge_masks(masks)
        self.array = array


class EventMarker(MultistateDerivedParameterNode):
    '''
    Combine Event Marker from multiple sources where recorded separately.
    '''
    values_mapping = {0: '-', 1: 'Event'}
    name = 'Event Marker'

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               event_marker_1=M('Event Marker (1)'),
               event_marker_2=M('Event Marker (2)'),
               event_marker_3=M('Event Marker (3)'),
               event_marker_capt=M('Event Marker (Capt)'),
               event_marker_fo=M('Event Marker (FO)')):

        self.array = vstack_params_where_state(
            (event_marker_1, 'Event'),
            (event_marker_2, 'Event'),
            (event_marker_3, 'Event'),
            (event_marker_capt, 'Event'),
            (event_marker_fo, 'Event'),
        ).any(axis=0)


class Flap(MultistateDerivedParameterNode):
    '''
    Steps raw Flap angle from surface into detents.
    '''

    units = 'deg'

    @classmethod
    def can_operate(cls, available, frame=A('Frame')):
        '''
        can operate with Frame and Alt aal if herc or Flap surface
        '''
        frame_name = frame.value if frame else None
        
        if frame_name == 'L382-Hercules':
            return 'Altitude AAL' in available
        
        return all_of(('Flap Angle', 'Series', 'Family'), available)

    def derive(self,
               flap=P('Flap Angle'),
               series=A('Series'),
               family=A('Family'),
               frame=A('Frame'),
               alt_aal=P('Altitude AAL')):

        frame_name = frame.value if frame else None

        if frame_name == 'L382-Hercules':
            self.values_mapping = {0: '0', 50: '50', 100: '100'}
            
            # Flap is not recorded, so invent one of the correct length.
            flap_herc = np_ma_zeros_like(alt_aal.array)

            # Takeoff is normally with 50% flap382
            _, toffs = slices_from_to(alt_aal.array, 0.0, 1000.0)
            flap_herc[:toffs[0].stop] = 50.0

            # Assume 50% from 2000 to 1000ft, and 100% thereafter on the approach.
            _, apps = slices_from_to(alt_aal.array, 2000.0, 0.0)
            flap_herc[apps[-1].start:] = np.ma.where(alt_aal.array[apps[-1].start:]>1000.0,50.0,100.0)

            self.array = np.ma.array(flap_herc)
            self.frequency, self.offset = alt_aal.frequency, alt_aal.offset
            return
        try:
            flap_steps = get_flap_map(series.value, family.value)
        except KeyError:
            # no flaps mapping, round to nearest 5 degrees
            self.warning("No flap settings - rounding to nearest 5")
            # round to nearest 5 degrees
            array = round_to_nearest(flap.array, 5.0)
            flap_steps = [int(f) for f in np.ma.unique(array) if f is not np.ma.masked]
        self.values_mapping = {f: str(f) for f in flap_steps}
        self.array = step_values(repair_mask(flap.array), flap_steps, 
                                 flap.hz, step_at='move_start')


class FlapExcludingTransition(MultistateDerivedParameterNode):
    '''
    Specifically designed to cater for maintenance monitoring, this assumes
    that when moving the lower of the start and endpoints of the movement
    apply. This minimises the chance of needing a flap overspeed inspection.
    '''

    units = 'deg'

    def derive(self, flap=P('Flap Angle'), 
               series=A('Series'), family=A('Family')):
        self.values_mapping = get_flap_values_mapping(series, family, flap)
        self.array = step_values(repair_mask(flap.array),
                                 self.values_mapping.keys(),
                                 flap.hz, step_at='excluding_transition')


class FlapIncludingTransition(MultistateDerivedParameterNode):
    '''
    Specifically designed to cater for maintenance monitoring, this assumes
    that when moving the higher of the start and endpoints of the movement
    apply. This increases the chance of needing a flap overspeed inspection,
    but provides a more cautious interpretation of the maintenance
    requirements.
    '''

    units = 'deg'

    def derive(self, flap=P('Flap Angle'), 
               series=A('Series'), family=A('Family')):
        self.values_mapping = get_flap_values_mapping(series, family, flap)
        self.array = step_values(repair_mask(flap.array),
                                 self.values_mapping.keys(),
                                 flap.hz, step_at='including_transition')
            
            
class FlapLever(MultistateDerivedParameterNode):
    '''
    Rounds the Flap Lever Angle to the selected detent at the start of the
    angle movement.
    
    Flap is not used to synthesize Flap Lever as this could be misleading.
    Instead, all safety Key Point Values will use Flap Lever followed by Flap 
    if Flap Lever is not available.
    '''

    units = 'deg'

    def derive(self, flap_lever=P('Flap Lever Angle'),
               series=A('Series'), family=A('Family')):
        self.values_mapping = get_flap_values_mapping(series, family, flap_lever)
        # Take the moment the flap starts to move.
        self.array = step_values(repair_mask(flap_lever.array),
                                 self.values_mapping.keys(),
                                 flap_lever.hz, step_at='move_start')


class Flaperon(MultistateDerivedParameterNode):
    '''
    Where Ailerons move together and used as Flaps, these are known as
    "Flaperon" control.
    
    Flaperons are measured where both Left and Right Ailerons move down,
    which on the left creates possitive roll but on the right causes negative
    roll. The difference of the two signals is the Flaperon control.
    
    The Flaperon is stepped at the start of movement into the nearest aileron 
    detents, e.g. 0, 5, 10 deg
    
    Note: This is used for Airbus models and does not necessarily mean as
    much to other aircraft types.
    '''
    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family')):
        try:
            get_aileron_map(series.value, family.value)
        except KeyError:
            return False
        return 'Aileron (L)' in available and 'Aileron (R)' in available
    
    def derive(self, al=P('Aileron (L)'), ar=P('Aileron (R)'),
               series=A('Series'), family=A('Family')):
        # Take the difference of the two signals (which should cancel each
        # other out when rolling) and divide the range by two (to account for
        # the left going negative and right going positive when flaperons set)
        flaperon_angle = (al.array - ar.array) / 2
        ail_steps = get_aileron_map(series.value, family.value)
        self.values_mapping = {int(f): str(f) for f in ail_steps}
        self.array = step_values(flaperon_angle, ail_steps,
                                 al.hz, step_at='move_start')


class FuelQty_Low(MultistateDerivedParameterNode):
    '''
    '''
    name = "Fuel Qty (*) Low"
    values_mapping = {
        0: '-',
        1: 'Warning',
    }
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Fuel Qty Low', 'Fuel Qty (1) Low', 'Fuel Qty (2) Low'),
                      available)
        
    def derive(self, fqty = M('Fuel Qty Low'),
               fqty1 = M('Fuel Qty (1) Low'),
               fqty2 = M('Fuel Qty (2) Low')):
        warning = vstack_params_where_state(
            (fqty,  'Warning'),
            (fqty1, 'Warning'),
            (fqty2, 'Warning'),
        )
        self.array = warning.any(axis=0)


class GearDown(MultistateDerivedParameterNode):
    '''
    This Multi-State parameter uses "majority voting" to decide whether the
    gear is up or down.
    
    If Gear (*) Down is not recorded, it will be created from Gear Down
    Selected which is from the cockpit lever.
    
    TODO: Add a transit delay (~10secs) to the selection to when the gear is
    down.
    '''

    align = False
    values_mapping = {
        0: 'Up',
        1: 'Down',
    }

    @classmethod
    def can_operate(cls, available):
        # Can operate with a any combination of parameters available
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               gl=M('Gear (L) Down'),
               gn=M('Gear (N) Down'),
               gr=M('Gear (R) Down'),
               gear_sel=M('Gear Down Selected')):
        # Join all available gear parameters and use whichever are available.
        if gl or gn or gr:
            v = vstack_params(gl, gn, gr)
            wheels_down = v.sum(axis=0) >= (v.shape[0] / 2.0)
            self.array = np.ma.where(wheels_down, self.state['Down'], self.state['Up'])
        else:
            self.array = gear_sel.array


class GearOnGround(MultistateDerivedParameterNode):
    '''
    Combination of left and right main gear signals.
    '''
    align = False
    values_mapping = {
        0: 'Air',
        1: 'Ground',
    }

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               gl=M('Gear (L) On Ground'),
               gr=M('Gear (R) On Ground')):

        # Note that this is not needed on the following frames which record
        # this parameter directly: 737-4, 737-i

        if gl and gr:
            delta = abs((gl.offset - gr.offset) * gl.frequency)
            if 0.75 < delta or delta < 0.25:
                # If the samples of the left and right gear are close together,
                # the best representation is to map them onto a single
                # parameter in which we accept that either wheel on the ground
                # equates to gear on ground.
                self.array = np.ma.logical_or(gl.array, gr.array)
                self.frequency = gl.frequency
                self.offset = gl.offset
                return
            else:
                # If the paramters are not co-located, then
                # merge_two_parameters creates the best combination possible.
                self.array, self.frequency, self.offset = merge_two_parameters(gl, gr)
                return
        if gl:
            gear = gl
        else:
            gear = gr
        self.array = gear.array
        self.frequency = gear.frequency
        self.offset = gear.offset


class GearDownSelected(MultistateDerivedParameterNode):
    '''
    Derivation of gear selection for aircraft without this separately recorded.
    Where 'Gear Down Selected' is recorded, this derived parameter will be
    skipped automatically.

    This is the inverse of 'Gear Up Selected' which does all the hard work
    for us establishing transitions from 'Gear Down' with the assocaited Red
    Warnings.
    '''

    values_mapping = {
        0: 'Up',
        1: 'Down',
    }

    def derive(self, gear_up_sel=P('Gear Up Selected')):
        # Invert the Gear Up Selected array
        #Q: which is easier to understand?!
        #self.array = np.ma.where(gear_up_sel.array == 'Up', 'Down', 'Up')
        self.array = 1 - gear_up_sel.array.raw


class GearUpSelected(MultistateDerivedParameterNode):
    '''
    Derivation of gear selection for aircraft without this separately recorded.
    Where 'Gear Up Selected' is recorded, this derived parameter will be
    skipped automatically.

    Red warnings are included as the selection may first be indicated by one
    of the red warning lights coming on, rather than the gear status
    changing.
    
    This is the basis for 'Gear Down Selected'.
    
    TODO: Add a transit delay (~10secs) to the selection to when the gear down.
    TODO: Derive from "Gear Up" only if recorded.
    '''

    values_mapping = {
        0: 'Down',
        1: 'Up',
    }

    @classmethod
    def can_operate(cls, available):
        return 'Gear Down' in available

    def derive(self,
               gear_down=M('Gear Down'),
               gear_warn=M('Gear (*) Red Warning')):
        # use the inverse of the gear down array as a start
        self.array = gear_down.array != 'Down'  # True for 'Up'
        if gear_warn:
            start_warning = find_edges_on_state_change(
                'Warning', gear_warn.array)
            last = 0
            state = 'Down'
            for start in start_warning:
                # for clarity, we're only interested in the start of the
                # transition - so ceiling finds the start
                start = math.ceil(start)
                # look for state before gear started moving (back one sample)
                state = 'Down' if gear_down.array[start-1] == 'Down' else 'Up'
                self.array[last:start+1] = state
                last = start
            else:
                # finish off the rest of the array with the inverse of the
                # last state
                if state == 'Down':
                    self.array[last:] = 'Up'
                else:
                    self.array[last:] = 'Down'


class Gear_RedWarning(MultistateDerivedParameterNode):
    '''
    Merges all the Red Warning systems for Nose, Left and Right gears.
    Ensures that false warnings on the ground are ignored.
    '''
    name = 'Gear (*) Red Warning'
    values_mapping = {0: '-',
                      1: 'Warning'}
    #store in hdf = False! glimpse into the future ;)
    
    @classmethod
    def can_operate(self, available):
        return 'Airborne' in available and any_of(
            ('Gear (L) Red Warning', 'Gear (N) Red Warning', 
             'Gear (R) Red Warning'), available)
    
    def derive(self, 
               gear_warn_l=M('Gear (L) Red Warning'),
               gear_warn_n=M('Gear (N) Red Warning'),
               gear_warn_r=M('Gear (R) Red Warning'),
               airs=S('Airborne')):
        # Join available gear parameters and use whichever are available.
        red_warning = vstack_params_where_state(
            (gear_warn_l, 'Warning'),
            (gear_warn_n, 'Warning'),
            (gear_warn_r, 'Warning'),
        )
        in_air = np.zeros(len(red_warning[0]), dtype=np.bool)
        for air in airs:
            in_air[air.slice] = 1
        # ensure that the red warnings were in the air
        ##gear_warn = M(array=red_warning.any(axis=0), values_mapping={
            ##True: 'Warning'})
        red_air = red_warning.any(axis=0) & in_air
        # creating mapped array is probably not be required due to __setattr__
        self.array = MappedArray(red_air, values_mapping=self.values_mapping)


class ILSInnerMarker(MultistateDerivedParameterNode):
    '''
    Combine ILS Marker for captain and first officer where recorded separately.
    '''
    values_mapping = {0: '-', 1: 'Present'}
    align = False
    name = 'ILS Inner Marker'

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, 
               ils_mkr_capt=M('ILS Inner Marker (Capt)'),
               ils_mkr_fo=M('ILS Inner Marker (FO)')):

        self.array = vstack_params_where_state(
            (ils_mkr_capt, 'Present'),
            (ils_mkr_fo, 'Present'),
        ).any(axis=0)


class ILSMiddleMarker(MultistateDerivedParameterNode):
    '''
    Combine ILS Marker for captain and first officer where recorded separately.
    '''
    values_mapping = {0: '-', 1: 'Present'}
    align = False
    name = 'ILS Middle Marker'

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, 
               ils_mkr_capt=M('ILS Middle Marker (Capt)'),
               ils_mkr_fo=M('ILS Middle Marker (FO)')):

        self.array = vstack_params_where_state(
            (ils_mkr_capt, 'Present'),
            (ils_mkr_fo, 'Present'),
        ).any(axis=0)


class ILSOuterMarker(MultistateDerivedParameterNode):
    '''
    Combine ILS Marker for captain and first officer where recorded separately.
    '''
    values_mapping = {0: '-', 1: 'Present'}
    align = False
    name = 'ILS Outer Marker'

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, 
               ils_mkr_capt=M('ILS Outer Marker (Capt)'),
               ils_mkr_fo=M('ILS Outer Marker (FO)')):

        self.array = vstack_params_where_state(
            (ils_mkr_capt, 'Present'),
            (ils_mkr_fo, 'Present'),
        ).any(axis=0)


class KeyVHFCapt(MultistateDerivedParameterNode):
    
    name = 'Key VHF (Capt)'
    values_mapping = {0: '-', 1: 'Keyed'}

    @classmethod
    def can_operate(cls, available):
        return any_of(('Key VHF (1) (Capt)',
                       'Key VHF (2) (Capt)',
                       'Key VHF (3) (Capt)'), available)

    def derive(self, key_vhf_1=M('Key VHF (1) (Capt)'),
               key_vhf_2=M('Key VHF (2) (Capt)'),
               key_vhf_3=M('Key VHF (3) (Capt)')):
        self.array = vstack_params_where_state(
            (key_vhf_1, 'Keyed'),
            (key_vhf_2, 'Keyed'),
            (key_vhf_3, 'Keyed'),
        ).any(axis=0)


class KeyVHFFO(MultistateDerivedParameterNode):
    
    name = 'Key VHF (FO)'
    values_mapping = {0: '-', 1: 'Keyed'}

    @classmethod
    def can_operate(cls, available):
        return any_of(('Key VHF (1) (FO)',
                       'Key VHF (2) (FO)',
                       'Key VHF (3) (FO)'), available)

    def derive(self, key_vhf_1=M('Key VHF (1) (FO)'),
               key_vhf_2=M('Key VHF (2) (FO)'),
               key_vhf_3=M('Key VHF (3) (FO)')):
        self.array = vstack_params_where_state(
            (key_vhf_1, 'Keyed'),
            (key_vhf_2, 'Keyed'),
            (key_vhf_3, 'Keyed'),
        ).any(axis=0)


class MasterWarning(MultistateDerivedParameterNode):
    '''
    Combine master warning for captain and first officer.
    '''
    values_mapping = {0: '-', 1: 'Warning'}

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self, 
               warn_capt=M('Master Warning (Capt)'),
               warn_fo=M('Master Warning (FO)')):

        self.array = vstack_params_where_state(
            (warn_capt, 'Warning'),
            (warn_fo, 'Warning'),
        ).any(axis=0)


class PackValvesOpen(MultistateDerivedParameterNode):
    '''
    Integer representation of the combined pack configuration.
    '''

    name = 'Pack Valves Open'

    values_mapping = {
        0: 'All closed',
        1: 'One engine low flow',
        2: 'Flow level 2',
        3: 'Flow level 3',
        4: 'Both engines high flow',
    }

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        # Works with both 'ECS Pack (1) On' and 'ECS Pack (2) On' ECS Pack High Flows are optional
        return all_of(['ECS Pack (1) On', 'ECS Pack (2) On' ], available)

    def derive(self,
            p1=M('ECS Pack (1) On'), p1h=M('ECS Pack (1) High Flow'),
            p2=M('ECS Pack (2) On'), p2h=M('ECS Pack (2) High Flow')):
        '''
        '''
        # TODO: account properly for states/frame specific fixes
        # Sum the open engines, allowing 1 for low flow and 1+1 for high flow
        # each side.
        flow = p1.array.raw + p2.array.raw
        if p1h and p2h:
            flow = p1.array.raw * (1 + p1h.array.raw) \
                 + p2.array.raw * (1 + p2h.array.raw)
        self.array = flow
        self.offset = offset_select('mean', [p1, p1h, p2, p2h])


class PilotFlying(MultistateDerivedParameterNode):
    '''
    Determines the pilot flying for Airbus aircraft.
    '''
    values_mapping = {0: '-', 1: 'Capt', 2: 'FO'}

    def derive(self,
               stick_capt=P('Sidestick Angle (Capt)'),
               stick_fo=P('Sidestick Angle (FO)')):

        pilot_flying = MappedArray(np.ma.zeros(stick_capt.array.size),
                                   values_mapping=self.values_mapping)

        if stick_capt.array.size > 61:
            # Calculate average instead of sum as it we already have a function
            # defined to work over a window and it doesn't affect the result as
            # the arrays are altered in the same way and are still comparable.
            window = 61 * self.hz  # Use 61 seconds for 30 seconds either side.
            angle_capt = moving_average(stick_capt.array, window)
            angle_fo = moving_average(stick_fo.array, window)
            # Repair the array as the moving average is padded with masked
            # zeros
            angle_capt = repair_mask(angle_capt, repair_duration=31,
                                     extrapolate=True)
            angle_fo = repair_mask(angle_fo, repair_duration=31,
                                   extrapolate=True)

            pilot_flying[angle_capt > angle_fo] = 'Capt'
            pilot_flying[angle_capt < angle_fo] = 'FO'

        self.array = pilot_flying


class PitchAlternateLaw(MultistateDerivedParameterNode):
    '''
    Combine Pitch Alternate Law from sources (1) and/or (2).
    
    TODO: Review
    '''
    values_mapping = {0: '-', 1: 'Alternate'}

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               alt_law_1=M('Pitch Alternate (1) Law'),
               alt_law_2=M('Pitch Alternate (2) Law')):

        self.array = vstack_params_where_state(
            (alt_law_1, 'Alternate'),
            (alt_law_2, 'Alternate'),
        ).any(axis=0)


class Slat(MultistateDerivedParameterNode):
    """
    Steps raw Slat angle into detents.
    """

    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family')):
        try:
            get_slat_map(series.value, family.value)
        except KeyError:
            return False
        return all_of(['Slat Surface', 'Series', 'Family'], available)
    
    def derive(self, slat=P('Slat Surface'), series=A('Series'), family=A('Family')):
        slat_steps = get_slat_map(series.value, family.value)
        # No longer support rounding to nearest
        ##except KeyError:
            ### no slats mapping, round to nearest 5 degrees
            ##self.warning("No slat settings - rounding to nearest 5")
            ### round to nearest 5 degrees
            ##self.array = round_to_nearest(slat.array, 5.0)
        self.values_mapping = {int(f): str(f) for f in slat_steps}
        self.array = step_values(slat.array, slat_steps,
                                 slat.hz, step_at='move_start')
            

class StickPusher(MultistateDerivedParameterNode):
    '''
    Merge left and right stick pushers where fitted.
    '''

    values_mapping = {
        0: '-',
        1: 'Push'
    }

    @classmethod
    def can_operate(cls, available):
        return any_of(('Stick Pusher (L)',
                       'Stick Pusher (R)'
                       ),available)

    def derive(self, spl = M('Stick Pusher (L)'),
               spr=M('Stick Pusher (R)')):

        available = [par for par in [spl, spr] if par]

        if len(available) > 1:
            self.array = merge_sources(*[a.array for a in available])
            self.offset = min([a.offset for a in available])
            self.frequency = available[0].frequency * len(available)
        elif len(available) == 1:
            self.array = available[0].array


class StickShaker(MultistateDerivedParameterNode):
    '''
    This accounts for the different types of stick shaker system. Where two
    systems are recorded the results are OR'd to make a single parameter which
    operates in response to either system triggering. Hence the removal of
    automatic alignment of the signals.
    '''

    align = False
    values_mapping = {
        0: '-',
        1: 'Shake',
    }

    @classmethod
    def can_operate(cls, available):
        return any_of(('Stick Shaker (L)',
                       'Stick Shaker (R)',
                       'Stick Shaker (1)',
                       'Stick Shaker (2)',
                       'Stick Shaker (3)',
                       'Stick Shaker (4)',
                       #'Stick Shaker (L) (1)',
                       #'Stick Shaker (L) (2)',
                       #'Stick Shaker (R) (1)',
                       #'Stick Shaker (R) (2)',
                       ),available)
    
    def derive(self, ssl = M('Stick Shaker (L)'),
               ssr=M('Stick Shaker (R)'),
               ss1=M('Stick Shaker (1)'),
               ss2=M('Stick Shaker (2)'),
               ss3=M('Stick Shaker (3)'),
               ss4=M('Stick Shaker (4)'),
               frame=A('Frame'),
               #b777_L1=M('Stick Shaker (L) (1)'),
               #b777_L2=M('Stick Shaker (L) (2)'),
               #b777_R1=M('Stick Shaker (R) (1)'),
               #b777_R2=M('Stick Shaker (R) (2)'),
               ):
        
        if frame and frame.value=='B777':
            #Provision has been included for Boeing 777 type, but until this has been
            #evaluated in detail it raises an exception because there are two bits per
            #shaker, and their operation is not obvious from the documentation.
            raise ValueError
        
        available = [par for par in [ssl, ssr, ss1, ss2, ss3, ss4,
                                     #b777_L1, b777_L2, b777_R1, b777_R2,
                                     ] if par]
        if len(available) > 1:
            self.array = merge_sources(*[a.array for a in available])
            self.offset = min([a.offset for a in available])
            self.frequency = available[0].frequency*len(available)
        elif len(available) == 1:
            self.array = available[0].array
            self.offset = available[0].offset
            self.frequency = available[0].frequency        


class SpeedbrakeSelected(MultistateDerivedParameterNode):
    '''
    Determines the selected state of the speedbrake.

    Speedbrake Selected Values:

    - 0 -- Stowed
    - 1 -- Armed / Commanded (Spoilers Down)
    - 2 -- Deployed / Commanded (Spoilers Up)
    '''

    values_mapping = {
        0: 'Stowed',
        1: 'Armed/Cmd Dn',
        2: 'Deployed/Cmd Up',
    }

    @classmethod
    def can_operate(cls, available):
        '''
        '''
        x = available
        return 'Speedbrake Deployed' in x \
               or ('Family' in x and 'Spoiler Switch' in x)\
               or ('Family' in x and 'Speedbrake Handle' in x)\
               or ('Family' in x and 'Speedbrake' in x)

    def a320_speedbrake(self, armed, spdbrk):
        '''
        Speedbrake operation for A320 family.
        '''
        array = np.ma.where(spdbrk.array > 1.0,
                            'Deployed/Cmd Up', armed.array)
        return array
    
    def b737_speedbrake(self, spdbrk, handle):
        '''
        Speedbrake Handle Positions for 737-x:

            ========    ============
            Angle       Notes
            ========    ============
             0.0        Full Forward
             4.0        Armed
            24.0
            29.0
            38.0        In Flight
            40.0        Straight Up
            48.0        Full Up
            ========    ============

        Speedbrake Positions > 1 = Deployed
        '''
        if spdbrk and handle:
            # Speedbrake and Speedbrake Handle available
            '''
            Speedbrake status taken from surface position. This allows
            for aircraft where the handle is inoperative, overwriting
            whatever the handle position is when the brakes themselves
            have deployed.

            It's not possible to identify when the speedbrakes are just
            armed in this case, so we take any significant motion as
            deployed.

            If there is no handle position recorded, the default 'Stowed'
            value is retained.
            '''
            armed = np.ma.where((2.0 < handle.array) & (handle.array < 35.0),
                                'Armed/Cmd Dn', 'Stowed')
            array = np.ma.where((handle.array >= 35.0) | (spdbrk.array > 1.0),
                                'Deployed/Cmd Up', armed)
        elif spdbrk and not handle:
            # Speedbrake only
            array = np.ma.where(spdbrk.array > 1.0,
                                'Deployed/Cmd Up', 'Stowed')
        elif handle and not spdbrk:
            # Speedbrake Handle only
            armed = np.ma.where((2.0 < handle.array) & (handle.array < 35.0),
                                'Armed/Cmd Dn', 'Stowed')
            array = np.ma.where(handle.array >= 35.0,
                                'Deployed/Cmd Up', armed)
        else:
            raise ValueError("Can't work without either Speedbrake or Handle")
        return array

    def b757_767_speedbrake(self, handle):
        '''
        Speedbrake Handle Positions for 757 & 767:

            ========    ============
              %           Notes
            ========    ============
               0.0        Full Forward
              15.0        Armed
             100.0        Full Up
            ========    ============
        '''
        # Speedbrake Handle only
        armed = np.ma.where((12.0 < handle.array) & (handle.array < 25.0),
                            'Armed/Cmd Dn', 'Stowed')
        array = np.ma.where(handle.array >= 25.0,
                            'Deployed/Cmd Up', armed)
        return array
    
    @staticmethod
    def b787_speedbrake(handle):
        '''
        Speedbrake Handle Positions for 787, taken from early recordings. 
        '''
        # Speedbrake Handle only
        speedbrake = np.ma.zeros(len(handle.array))
        stepped_array = step_values(handle.array, [0, 10, 20])
        # Assuming all values from 15 and above are Deployed. Typically a
        # maximum value of 60 is recorded when deployed with reverse thrust 
        # whereas values of 30-60 are seen during the approach.
        speedbrake[stepped_array == 10] = 1
        speedbrake[stepped_array == 20] = 2
        return speedbrake
    
    @staticmethod
    def learjet_speedbrake(spdsw):
        '''
        Learjet 60XS has a switch with settings:
        0 = Retract
        4 = Extended
        7 = Armed
        6 = Partial
        
        Here we map thus:
            Retract = Stowed
            Armed = Armed/Cmd Dn
            Partial or Extended = Deployed/Cmd Up
        '''
        switch = spdsw.array
        speedbrake = np_ma_zeros_like(switch)
        speedbrake = np.ma.where(switch=='Retract', 'Stowed',
                                 'Deployed/Cmd Up')
        speedbrake = np.ma.where(switch=='Armed', 'Armed/Cmd Dn',
                                 speedbrake)
        return speedbrake

    def derive(self,
               deployed=M('Speedbrake Deployed'),
               armed=M('Speedbrake Armed'),
               handle=P('Speedbrake Handle'),
               spdbrk=P('Speedbrake'),
               spdsw=M('Spoiler Switch'),
               family=A('Family')):

        family_name = family.value if family else ''

        if deployed:
            # We have a speedbrake deployed discrete. Set initial state to
            # stowed, then set armed states if available, and finally set
            # deployed state:
            array = np.ma.zeros(len(deployed.array))
            if armed:
                array[armed.array == 'Armed'] = 1
            array[deployed.array == 'Deployed'] = 2
            self.array = array

        elif 'B737' in family_name:
            self.array = self.b737_speedbrake(spdbrk, handle)

        elif family_name in ['B757', 'B767']:
            self.array = self.b757_767_speedbrake(handle)

        elif family_name == 'B787':
            self.array = self.b787_speedbrake(handle)

        elif family_name == 'A320':
            self.array = self.a320_speedbrake(armed, spdbrk)

        elif family_name == 'Learjet':
            self.array = self.learjet_speedbrake(spdsw)

        elif family_name == 'G-V':
            # On the test aircraft SE-RDY the Speedbrake stored 0 at all
            # times and Speedbrake Handle was unresponsive with small numeric
            # variation. The Speedbrake (L) & (R) responded normally so we
            # simply accept over 30deg as deployed.
            self.array = np.ma.where(spdbrk.array < 2.0,
                                     'Stowed',
                                     'Deployed/Cmd Up')

        elif family_name in['Global', 'CL-600', 'CRJ 100/200', 'ERJ-135/145']:
            # No valid data seen for this type to date....
            logger.warning(
                'SpeedbrakeSelected: algorithm for family `%s` is undecided, '
                'temporarily using speedbrake handle.', family_name)
            self.array = np_ma_masked_zeros_like(handle.array)

        elif family_name in['A340']:
            # We don't have the "Armed" state
            self.array = handle.array * 2

        else:
            raise NotImplementedError


class StableApproach(MultistateDerivedParameterNode):
    '''
    During the Approach, the following steps are assessed for stability:

    1. Gear is down
    2. Landing Flap is set
    3. Heading aligned to Runway within 10 degrees
    4. Approach Airspeed minus Reference speed within 20 knots
    5. Glideslope deviation within 1 dot
    6. Localizer deviation within 1 dot
    7. Vertical speed between -1000 and -200 fpm
    8. Engine Power greater than 45% # TODO: and not Cycling within last 5 seconds

    if all the above steps are met, the result is the declaration of:
    9. "Stable"
    
    If Vapp is recorded, a more constraint airspeed threshold is applied.
    Where parameters are not monitored below a certain threshold (e.g. ILS
    below 200ft) the stability criteria just before 200ft is reached is
    continued through to landing. So if one was unstable due to ILS
    Glideslope down to 200ft, that stability is assumed to continue through
    to landing.

    TODO/REVIEW:
    ============
    * Check for 300ft limit if turning onto runway late and ignore stability criteria before this? Alternatively only assess criteria when heading is within 50.
    * Q: Fill masked values of parameters with False (unstable: stop here) or True (stable, carry on)
    * Add hysteresis (3 second gliding windows for GS / LOC etc.)
    * Engine cycling check
    * Check Boeing's Vref as one may add an increment to this (20kts) which is not recorded!
    '''

    values_mapping = {
        0: '-',  # All values should be masked anyway, this helps align values
        1: 'Gear Not Down',
        2: 'Not Landing Flap',
        3: 'Hdg Not Aligned',   # Rename with heading?
        4: 'Aspd Not Stable',  # Q: Split into two Airspeed High/Low?
        5: 'GS Not Stable',
        6: 'Loc Not Stable',
        7: 'IVV Not Stable',
        8: 'Eng N1 Not Stable',
        9: 'Stable',
    }

    align_frequency = 1  # force to 1Hz

    @classmethod
    def can_operate(cls, available):
        # Commented out optional dependencies
        # Airspeed Relative, ILS and Vapp are optional
        deps = ['Approach And Landing', 'Gear Down', 'Flap', 
                'Track Deviation From Runway',
                #'Airspeed Relative For 3 Sec', 
                'Vertical Speed', 
                #'ILS Glideslope', 'ILS Localizer',
                #'Eng (*) N1 Min For 5 Sec', 
                'Altitude AAL',
                #'Vapp',
                ]
        return all_of(deps, available) and (
            'Eng (*) N1 Min For 5 Sec' in available or \
            'Eng (*) EPR Min For 5 Sec' in available)
    
    def derive(self,
               apps=S('Approach And Landing'),
               gear=M('Gear Down'),
               flap=M('Flap'),
               tdev=P('Track Deviation From Runway'),
               aspd=P('Airspeed Relative For 3 Sec'),
               vspd=P('Vertical Speed'),
               gdev=P('ILS Glideslope'),
               ldev=P('ILS Localizer'),
               eng_n1=P('Eng (*) N1 Min For 5 Sec'),
               eng_epr=P('Eng (*) EPR Min For 5 Sec'),
               alt=P('Altitude AAL'),
               vapp=P('Vapp'),
               family=A('Family')):
      
        #Ht AAL due to
        # the altitude above airfield level corresponding to each cause
        # options are FLAP, GEAR GS HI/LO, LOC, SPD HI/LO and VSI HI/LO

        # create an empty fully masked array
        self.array = np.ma.zeros(len(alt.array))
        self.array.mask = True
        # shortcut for repairing masks
        repair = lambda ar, ap: repair_mask(ar[ap], zero_if_masked=True)

        for approach in apps:
            # Restrict slice to 10 seconds after landing if we hit the ground
            gnd = index_at_value(alt.array, 0, approach.slice)
            if gnd and gnd + 10 < approach.slice.stop:
                stop = gnd + 10
            else:
                stop = approach.slice.stop
            _slice = slice(approach.slice.start, stop)
            # prepare data for this appproach:
            gear_down = repair(gear.array, _slice)
            flap_lever = repair(flap.array, _slice)
            track_dev = repair(tdev.array, _slice)
            airspeed = repair(aspd.array, _slice) if aspd else None  # optional
            glideslope = repair(gdev.array, _slice) if gdev else None  # optional
            localizer = repair(ldev.array, _slice) if ldev else None  # optional
            # apply quite a large moving average to smooth over peaks and troughs
            vertical_speed = moving_average(repair(vspd.array, _slice), 10)
            if eng_epr:
                # use EPR if available
                engine = repair(eng_epr.array, _slice)
            else:
                engine = repair(eng_n1.array, _slice)
            altitude = repair(alt.array, _slice)
            
            index_at_50 = index_closest_value(altitude, 50)
            index_at_200 = index_closest_value(altitude, 200)

            # Determine whether Glideslope was used at 1000ft, if not ignore ILS
            glide_est_at_1000ft = False
            if gdev and ldev:
                _1000 = index_at_value(altitude, 1000)
                if _1000:
                    # If masked at 1000ft; bool(np.ma.masked) == False
                    glide_est_at_1000ft = abs(glideslope[_1000]) < 1.5  # dots

            #== 1. Gear Down ==
            # Assume unstable due to Gear Down at first
            self.array[_slice] = 1
            landing_gear_set = (gear_down == 'Down')
            stable = landing_gear_set.filled(True)  # assume stable (gear down)

            #== 2. Landing Flap ==
            # not due to landing gear so try to prove it wasn't due to Landing Flap
            self.array[_slice][stable] = 2
            # look for maximum flap used in approach, otherwise go-arounds
            # can detect the start of flap retracting as the landing flap.
            landing_flap = np.ma.max(flap_lever)
            if landing_flap is not np.ma.masked:
                landing_flap_set = (flap_lever == landing_flap)
                # assume stable (flap set)
                stable &= landing_flap_set.filled(True)
            else:
                # All landing flap is masked, assume stable
                logger.warning(
                    'StableApproach: the landing flap is all masked in '
                    'the approach.')
                stable &= True

            #== 3. Heading ==
            self.array[_slice][stable] = 3
            STABLE_HEADING = 10  # degrees
            stable_track_dev = abs(track_dev) <= STABLE_HEADING
            stable &= stable_track_dev.filled(True)  # assume stable (on track)

            if aspd:
                #== 4. Airspeed Relative ==
                self.array[_slice][stable] = 4
                if vapp:
                    # Those aircraft which record a variable Vapp shall have more constraint thresholds
                    STABLE_AIRSPEED_BELOW_REF = -5
                    STABLE_AIRSPEED_ABOVE_REF = 10
                else:
                    # Most aircraft records only Vref - as we don't know the wind correction more lenient
                    STABLE_AIRSPEED_BELOW_REF = 0
                    STABLE_AIRSPEED_ABOVE_REF = 30
                stable_airspeed = (airspeed >= STABLE_AIRSPEED_BELOW_REF) & (airspeed <= STABLE_AIRSPEED_ABOVE_REF)
                # extend the stability at the end of the altitude threshold through to landing
                stable_airspeed[altitude < 50] = stable_airspeed[index_at_50]
                stable &= stable_airspeed.filled(True)  # if no V Ref speed, values are masked so consider stable as one is not flying to the vref speed??

            if glide_est_at_1000ft:
                #== 5. Glideslope Deviation ==
                self.array[_slice][stable] = 5
                STABLE_GLIDESLOPE = 1.0  # dots
                stable_gs = (abs(glideslope) <= STABLE_GLIDESLOPE)
                # extend the stability at the end of the altitude threshold through to landing
                stable_gs[altitude < 200] = stable_gs[index_at_200]
                stable &= stable_gs.filled(False)  # masked values are usually because they are way outside of range and short spikes will have been repaired

                #== 6. Localizer Deviation ==
                self.array[_slice][stable] = 6
                STABLE_LOCALIZER = 1.0  # dots
                stable_loc = (abs(localizer) <= STABLE_LOCALIZER)
                # extend the stability at the end of the altitude threshold through to landing
                stable_loc[altitude < 200] = stable_loc[index_at_200]
                stable &= stable_loc.filled(False)  # masked values are usually because they are way outside of range and short spikes will have been repaired

            #== 7. Vertical Speed ==
            self.array[_slice][stable] = 7
            STABLE_VERTICAL_SPEED_MIN = -1000
            STABLE_VERTICAL_SPEED_MAX = -200
            stable_vert = (vertical_speed >= STABLE_VERTICAL_SPEED_MIN) & (vertical_speed <= STABLE_VERTICAL_SPEED_MAX) 
            # extend the stability at the end of the altitude threshold through to landing
            stable_vert[altitude < 50] = stable_vert[index_at_50]
            stable &= stable_vert.filled(True)
            
            #== 8. Engine Power (N1) ==
            self.array[_slice][stable] = 8
            # TODO: Patch this value depending upon aircraft type
            if family and family.value == 'B787':
                STABLE_N1_MIN = 35 # %
            else:
                STABLE_N1_MIN = 45  # %
            STABLE_EPR_MIN = 1.1
            eng_minimum = STABLE_EPR_MIN if eng_epr else STABLE_N1_MIN
            stable_engine = (engine >= eng_minimum)
            stable_engine |= (altitude > 1000)  # Only use in altitude band below 1000 feet
            # extend the stability at the end of the altitude threshold through to landing
            stable_engine[altitude < 50] = stable_engine[index_at_50]
            stable &= stable_engine.filled(True)
            
            # TODO: Use Engine TPR instead of EPR if available.

            #== 9. Stable ==
            # Congratulations; whatever remains in this approach is stable!
            self.array[_slice][stable] = 9

        #endfor
        return


"""
class StickShaker(MultistateDerivedParameterNode):
    '''
    This accounts for the different types of stick shaker system. Where two
    systems are recorded the results are ORed to make a single parameter which
    operates in response to either system triggering. Hence the removal of
    automatic alignment of the signals.
    '''

    align = False
    values_mapping = {
        0: '-',
        1: 'Shake',
    }

    @classmethod
    def can_operate(cls, available):
        return ('Stick Shaker (L)' in available or \
                'Shaker Activation' in available)

    def derive(self, shake_l=M('Stick Shaker (L)'),
               shake_r=M('Stick Shaker (R)'),
               shake_act=M('Shaker Activation')):
        if shake_l and shake_r:
            self.array = np.ma.logical_or(shake_l.array, shake_r.array)
            self.frequency , self.offset = shake_l.frequency, shake_l.offset
        
        elif shake_l:
            # Named (L) but in fact (L) and (R) are or'd together at the DAU.
            self.array, self.frequency, self.offset = \
                shake_l.array, shake_l.frequency, shake_l.offset
        
        elif shake_act:
            self.array, self.frequency, self.offset = \
                shake_act.array, shake_act.frequency, shake_act.offset

        else:
            raise NotImplementedError
"""

class ThrustReversers(MultistateDerivedParameterNode):
    '''
    A single parameter with multi-state mapping as below.
    '''

    # We are interested in all stowed, all deployed or any other combination.
    # The mapping "In Transit" is used for anything other than the fully
    # established conditions, so for example one locked and the other not is
    # still treated as in transit.
    values_mapping = {
        0: 'Stowed',
        1: 'In Transit',
        2: 'Deployed',
    }

    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Eng (1) Thrust Reverser (L) Deployed',
            'Eng (1) Thrust Reverser (L) Unlocked',
            'Eng (1) Thrust Reverser (R) Deployed',
            'Eng (1) Thrust Reverser (R) Unlocked',
            'Eng (2) Thrust Reverser (L) Deployed',
            'Eng (2) Thrust Reverser (L) Unlocked',
            'Eng (2) Thrust Reverser (R) Deployed',
            'Eng (2) Thrust Reverser (R) Unlocked',
        ), available) or all_of((
            'Eng (1) Thrust Reverser Unlocked',
            'Eng (1) Thrust Reverser Deployed',
            'Eng (2) Thrust Reverser Unlocked',
            'Eng (2) Thrust Reverser Deployed',
        ), available) or all_of((
            'Eng (1) Thrust Reverser In Transit',
            'Eng (1) Thrust Reverser Deployed',
            'Eng (2) Thrust Reverser In Transit',
            'Eng (2) Thrust Reverser Deployed',
        ), available) or all_of((
            'Eng (1) Thrust Reverser',
            'Eng (2) Thrust Reverser',
        ), available)

    def derive(self,
            e1_dep_all=M('Eng (1) Thrust Reverser Deployed'),
            e1_dep_lft=M('Eng (1) Thrust Reverser (L) Deployed'),
            e1_dep_rgt=M('Eng (1) Thrust Reverser (R) Deployed'),
            e1_ulk_all=M('Eng (1) Thrust Reverser Unlocked'),
            e1_ulk_lft=M('Eng (1) Thrust Reverser (L) Unlocked'),
            e1_ulk_rgt=M('Eng (1) Thrust Reverser (R) Unlocked'),
            e1_tst_all=M('Eng (1) Thrust Reverser In Transit'),
            e2_dep_all=M('Eng (2) Thrust Reverser Deployed'),
            e2_dep_lft=M('Eng (2) Thrust Reverser (L) Deployed'),
            e2_dep_rgt=M('Eng (2) Thrust Reverser (R) Deployed'),
            e2_ulk_all=M('Eng (2) Thrust Reverser Unlocked'),
            e2_ulk_lft=M('Eng (2) Thrust Reverser (L) Unlocked'),
            e2_ulk_rgt=M('Eng (2) Thrust Reverser (R) Unlocked'),
            e2_tst_all=M('Eng (2) Thrust Reverser In Transit'),
            e3_dep_all=M('Eng (3) Thrust Reverser Deployed'),
            e3_dep_lft=M('Eng (3) Thrust Reverser (L) Deployed'),
            e3_dep_rgt=M('Eng (3) Thrust Reverser (R) Deployed'),
            e3_ulk_all=M('Eng (3) Thrust Reverser Unlocked'),
            e3_ulk_lft=M('Eng (3) Thrust Reverser (L) Unlocked'),
            e3_ulk_rgt=M('Eng (3) Thrust Reverser (R) Unlocked'),
            e3_tst_all=M('Eng (3) Thrust Reverser In Transit'),
            e4_dep_all=M('Eng (4) Thrust Reverser Deployed'),
            e4_dep_lft=M('Eng (4) Thrust Reverser (L) Deployed'),
            e4_dep_rgt=M('Eng (4) Thrust Reverser (R) Deployed'),
            e4_ulk_all=M('Eng (4) Thrust Reverser Unlocked'),
            e4_ulk_lft=M('Eng (4) Thrust Reverser (L) Unlocked'),
            e4_ulk_rgt=M('Eng (4) Thrust Reverser (R) Unlocked'),
            e4_tst_all=M('Eng (4) Thrust Reverser In Transit'),
            e1_status =M('Eng (1) Thrust Reverser'),
            e2_status =M('Eng (2) Thrust Reverser'),):

        deployed_params = (e1_dep_all, e1_dep_lft, e1_dep_rgt, e2_dep_all,
                           e2_dep_lft, e2_dep_rgt, e3_dep_all, e3_dep_lft,
                           e3_dep_rgt, e4_dep_all, e4_dep_lft, e4_dep_rgt,
                           e1_status, e2_status)

        deployed_stack = vstack_params_where_state(*[(d, 'Deployed') for d in deployed_params])

        unlocked_params = (e1_ulk_all, e1_ulk_lft, e1_ulk_rgt, e2_ulk_all,
                           e2_ulk_lft, e2_ulk_rgt, e3_ulk_all, e3_ulk_lft,
                           e3_ulk_rgt, e4_ulk_all, e4_ulk_lft, e4_ulk_rgt)

        array = np_ma_zeros_like(deployed_stack[0])
        stacks = [deployed_stack,]

        if any(unlocked_params):
            unlocked_stack = vstack_params_where_state(*[(p, 'Unlocked') for p in unlocked_params])
            array = np.ma.where(unlocked_stack.any(axis=0), 1, array)
            stacks.append(unlocked_stack)

        array = np.ma.where(deployed_stack.any(axis=0), 1, array)
        array = np.ma.where(deployed_stack.all(axis=0), 2, array)
        
        # update with any transit params
        if any((e1_tst_all, e2_tst_all, e3_tst_all, e4_tst_all)):
            transit_stack = vstack_params_where_state(
                (e1_tst_all, 'In Transit'), (e2_tst_all, 'In Transit'),
                (e3_tst_all, 'In Transit'), (e4_tst_all, 'In Transit'),
                (e1_status, 'In Transit'),  (e2_status, 'In Transit'), 
            )
            array = np.ma.where(transit_stack.any(axis=0), 1, array)
            stacks.append(transit_stack)

        mask_stack = np.ma.concatenate(stacks, axis=0)

        # mask indexes with greater than 50% masked values
        mask = np.ma.where(mask_stack.mask.sum(axis=0).astype(float)/len(mask_stack)*100 > 50, 1, 0)
        self.array = array
        self.array.mask = mask


class TAWSAlert(MultistateDerivedParameterNode):
    '''
    Merging all available TAWS alert signals into a single parameter for
    subsequent monitoring.
    '''
    name = 'TAWS Alert'
    values_mapping = {
        0: '-',
        1: 'Alert'}

    @classmethod
    def can_operate(cls, available):
        return any_of(['TAWS Caution Terrain',
                       'TAWS Caution',
                       'TAWS Dont Sink',
                       'TAWS Glideslope'
                       'TAWS Predictive Windshear',
                       'TAWS Pull Up',
                       'TAWS Sink Rate',
                       'TAWS Terrain',
                       'TAWS Terrain Warning Amber',
                       'TAWS Terrain Pull Up',
                       'TAWS Terrain Warning Red',
                       'TAWS Too Low Flap',
                       'TAWS Too Low Gear',
                       'TAWS Too Low Terrain',
                       'TAWS Windshear Warning',
                       ],
                      available)

    def derive(self, airs=S('Airborne'),
               taws_caution_terrain=M('TAWS Caution Terrain'),
               taws_caution=M('TAWS Caution'),
               taws_dont_sink=M('TAWS Dont Sink'),
               taws_glideslope=M('TAWS Glideslope'),
               taws_predictive_windshear=M('TAWS Predictive Windshear'),
               taws_pull_up=M('TAWS Pull Up'),
               taws_sink_rate=M('TAWS Sink Rate'),
               taws_terrain_pull_up=M('TAWS Terrain Pull Up'),
               taws_terrain_warning_amber=M('TAWS Terrain Warning Amber'),
               taws_terrain_warning_red=M('TAWS Terrain Warning Red'),
               taws_terrain=M('TAWS Terrain'),
               taws_too_low_flap=M('TAWS Too Low Flap'),
               taws_too_low_gear=M('TAWS Too Low Gear'),
               taws_too_low_terrain=M('TAWS Too Low Terrain'),
               taws_windshear_warning=M('TAWS Windshear Warning')):

        params_state = vstack_params_where_state(
            (taws_caution_terrain, 'Caution'),
            (taws_caution, 'Caution'),
            (taws_dont_sink, 'Warning'),
            (taws_glideslope, 'Warning'),
            (taws_predictive_windshear, 'Caution'),
            (taws_predictive_windshear, 'Warning'),
            (taws_pull_up, 'Warning'),
            (taws_sink_rate, 'Warning'),
            (taws_terrain_pull_up, 'Warning'),
            (taws_terrain_warning_amber, 'Warning'),
            (taws_terrain_warning_red, 'Warning'),
            (taws_terrain, 'Warning'),
            (taws_too_low_flap, 'Warning'),
            (taws_too_low_gear, 'Warning'),
            (taws_too_low_terrain, 'Warning'),
            (taws_windshear_warning, 'Warning'),
        )
        res = params_state.any(axis=0)

        self.array = np_ma_masked_zeros_like(params_state[0])
        if airs:
            for air in airs:
                self.array[air.slice] = res[air.slice]


class TAWSDontSink(MultistateDerivedParameterNode):
    name = 'TAWS Dont Sink'
    
    values_mapping = {
        0: '-',
        1: 'Warning',
    }
    
    @classmethod
    def can_operate(cls, available):
        return ('TAWS (L) Dont Sink' in available) or \
               ('TAWS (R) Dont Sink' in available)
    
    def derive(self, taws_l_dont_sink=M('TAWS (L) Dont Sink'),
               taws_r_dont_sink=M('TAWS (R) Dont Sink')):
        self.array = vstack_params_where_state(
            (taws_l_dont_sink, 'Warning'),
            (taws_r_dont_sink, 'Warning'),
        ).any(axis=0)


class TAWSGlideslopeCancel(MultistateDerivedParameterNode):
    name = 'TAWS Glideslope Cancel'
    
    values_mapping = {
        0: '-',
        1: 'Cancel',
    }
    
    @classmethod
    def can_operate(cls, available):
        return ('TAWS (L) Glideslope Cancel' in available) or \
               ('TAWS (R) Glideslope Cancel' in available)
    
    def derive(self, taws_l_gs=M('TAWS (L) Glideslope Cancel'),
               taws_r_gs=M('TAWS (R) Glideslope Cancel')):
        self.array = vstack_params_where_state(
            (taws_l_gs, 'Cancel'),
            (taws_r_gs, 'Cancel'),
        ).any(axis=0)


class TAWSTooLowGear(MultistateDerivedParameterNode):
    name = 'TAWS Too Low Gear'
        
    values_mapping = {
        0: '-',
        1: 'Warning',
    }
    
    @classmethod
    def can_operate(cls, available):
        return ('TAWS (L) Too Low Gear' in available) or \
               ('TAWS (R) Too Low Gear' in available)
    
    def derive(self, taws_l_gear=M('TAWS (L) Too Low Gear'),
               taws_r_gear=M('TAWS (R) Too Low Gear')):
        self.array = vstack_params_where_state(
            (taws_l_gear, 'Warning'),
            (taws_r_gear, 'Warning'),
        ).any(axis=0)


class TakeoffConfigurationWarning(MultistateDerivedParameterNode):
    '''
    Merging all available Takeoff Configuration Warning signals into a single
    parameter for subsequent monitoring.
    '''
    values_mapping = {
        0: '-',
        1: 'Warning',
    }
    
    @classmethod
    def can_operate(cls, available):
        return any_of(['Takeoff Configuration Stabilizer Warning',
                       'Takeoff Configuration Parking Brake Warning',
                       'Takeoff Configuration Flap Warning',
                       'Takeoff Configuration Gear Warning',
                       'Takeoff Configuration Rudder Warning',
                       'Takeoff Configuration Spoiler Warning'],
                      available)
    
    def derive(self, stabilizer=M('Takeoff Configuration Stabilizer Warning'),
               parking_brake=M('Takeoff Configuration Parking Brake Warning'),
               flap=M('Takeoff Configuration Flap Warning'),
               gear=M('Takeoff Configuration Gear Warning'),
               rudder=M('Takeoff Configuration Rudder Warning'),
               spoiler=M('Takeoff Configuration Rudder Warning')):
        params_state = vstack_params_where_state(
            (stabilizer, 'Warning'),
            (parking_brake, 'Warning'),
            (flap, 'Warning'),
            (gear, 'Warning'),
            (rudder, 'Warning'),
            (spoiler, 'Warning'))
        self.array = params_state.any(axis=0)


class TCASFailure(MultistateDerivedParameterNode):
    name = 'TCAS Failure'
        
    values_mapping = {
        0: '-',
        1: 'Failed',
    }
    
    @classmethod
    def can_operate(cls, available):
        return ('TCAS (L) Failure' in available) or \
               ('TCAS (R) Failure' in available)
    
    def derive(self, tcas_l_failure=M('TCAS (L) Failure'),
               tcas_r_failure=M('TCAS (R) Failure')):
        self.array = vstack_params_where_state(
            (tcas_l_failure, 'Failed'),
            (tcas_r_failure, 'Failed'),
        ).any(axis=0)
