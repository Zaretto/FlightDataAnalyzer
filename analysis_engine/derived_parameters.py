# -*- coding: utf-8 -*-

import numpy as np
import geomag

from math import ceil, radians
from scipy.interpolate import interp1d

from flightdatautilities.model_information import (get_conf_map,
                                                   get_flap_map)
from flightdatautilities.velocity_speed import get_vspeed_map
from flightdatautilities.vmo_mmo import get_vmo_procedure

from analysis_engine.exceptions import DataFrameError
from analysis_engine.node import (
    A, App, DerivedParameterNode, KPV, KTI, M, P, S,
)
from analysis_engine.library import (actuator_mismatch,
                                     air_track,
                                     align,
                                     all_of,
                                     any_of,
                                     alt2press,
                                     alt2sat,
                                     bearing_and_distance,
                                     bearings_and_distances,
                                     blend_parameters,
                                     blend_two_parameters,
                                     cas2dp,
                                     coreg,
                                     cycle_finder,
                                     dp2tas,
                                     dp_over_p2mach,
                                     filter_vor_ils_frequencies,
                                     first_valid_sample,
                                     first_order_lag,
                                     first_order_washout,
                                     ground_track,
                                     ground_track_precise,
                                     hysteresis,
                                     integrate,
                                     ils_localizer_align,
                                     index_at_value,
                                     interpolate,
                                     is_index_within_slice,
                                     last_valid_sample,
                                     latitudes_and_longitudes,
                                     localizer_scale,
                                     machtat2sat,
                                     mask_inside_slices,
                                     mask_outside_slices,
                                     match_altitudes,
                                     max_value,
                                     moving_average,
                                     np_ma_ones_like,
                                     np_ma_masked_zeros_like,
                                     np_ma_zeros_like,
                                     offset_select,
                                     peak_curvature,
                                     press2alt,
                                     rate_of_change,
                                     repair_mask,
                                     rms_noise,
                                     runway_deviation,
                                     runway_distances,
                                     runway_heading,
                                     runway_length,
                                     runway_snap_dict,
                                     second_window,
                                     shift_slice,
                                     slices_between,
                                     slices_from_to,
                                     slices_not,
                                     slices_or,
                                     smooth_track,
                                     straighten_headings,
                                     track_linking,
                                     value_at_index,
                                     vstack_params)

from settings import (AZ_WASHOUT_TC,
                      BOUNCED_LANDING_THRESHOLD,
                      FEET_PER_NM,
                      HYSTERESIS_FPIAS,
                      HYSTERESIS_FPROC,
                      GRAVITY_IMPERIAL,
                      KTS_TO_FPS,
                      KTS_TO_MPS,
                      METRES_TO_FEET,
                      METRES_TO_NM,
                      VERTICAL_SPEED_LAG_TC)

# There is no numpy masked array function for radians, so we just multiply thus:
deg2rad = radians(1.0)

class AccelerationLateralOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the lateral accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Lateral' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Lateral'),
               offset=KPV('Acceleration Lateral Offset')):
        if offset:
            self.array = acc.array - offset[0].value
        else:
            self.array = acc.array
            

class AccelerationLateralSmoothed(DerivedParameterNode):
    '''
    Apply a moving average for two seconds (9 samples) to smooth out spikes
    caused by uneven surfaces - especially noticable during cornering.
    '''
    units = 'g'
    
    def derive(self, acc=P('Acceleration Lateral Offset Removed')):
        self.window = acc.hz * 2 + 1  # store for ease of testing
        self.array = moving_average(acc.array, window=self.window)
    

class AccelerationLongitudinalOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the longitudinal accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Longitudinal' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Longitudinal'),
               offset=KPV('Acceleration Longitudinal Offset')):
        if offset:
            self.array = acc.array - offset[0].value
        else:
            self.array = acc.array


class AccelerationNormalOffsetRemoved(DerivedParameterNode):
    """
    This process attempts to remove datum errors in the normal accelerometer.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Acceleration Normal' in available

    units = 'g'

    def derive(self, acc=P('Acceleration Normal'),
               offset=KPV('Acceleration Normal Offset')):
        if offset:
            self.array = acc.array - offset[0].value + 1.0 # 1.0 to reset datum.
        else:
            self.array = acc.array


class AccelerationVertical(DerivedParameterNode):
    """
    Resolution of three accelerations to compute the vertical
    acceleration (perpendicular to the earth surface). Result is in g,
    retaining the 1.0 datum and positive upwards.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_lat=P('Acceleration Lateral Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch'), roll=P('Roll')):
        # FIXME: FloatingPointError: underflow encountered in multiply
        pitch_rad = pitch.array * deg2rad
        roll_rad = roll.array * deg2rad
        resolved_in_roll = acc_norm.array * np.ma.cos(roll_rad)\
            - acc_lat.array * np.ma.sin(roll_rad)
        self.array = resolved_in_roll * np.ma.cos(pitch_rad) \
                     + acc_long.array * np.ma.sin(pitch_rad)


class AccelerationForwards(DerivedParameterNode):
    """
    Resolution of three body axis accelerations to compute the forward
    acceleration, that is, in the direction of the aircraft centreline
    when projected onto the earth's surface.

    Forwards = +ve, Constant sensor errors not washed out.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch')):
        pitch_rad = pitch.array * deg2rad
        self.array = acc_long.array * np.ma.cos(pitch_rad)\
                     - acc_norm.array * np.ma.sin(pitch_rad)


class AccelerationAcrossTrack(DerivedParameterNode):
    """
    The forward and sideways ground-referenced accelerations are resolved
    into along track and across track coordinates in preparation for
    groundspeed computations.
    """

    units = 'g'

    def derive(self, acc_fwd=P('Acceleration Forwards'),
               acc_side=P('Acceleration Sideways'),
               drift=P('Drift')):
        drift_rad = drift.array*deg2rad
        self.array = acc_side.array * np.ma.cos(drift_rad)\
            - acc_fwd.array * np.ma.sin(drift_rad)


class AccelerationAlongTrack(DerivedParameterNode):
    """
    The forward and sideways ground-referenced accelerations are resolved
    into along track and across track coordinates in preparation for
    groundspeed computations.
    """

    units = 'g'

    def derive(self, acc_fwd=P('Acceleration Forwards'),
               acc_side=P('Acceleration Sideways'),
               drift=P('Drift')):
        drift_rad = drift.array*deg2rad
        self.array = acc_fwd.array * np.ma.cos(drift_rad)\
                     + acc_side.array * np.ma.sin(drift_rad)


class AccelerationSideways(DerivedParameterNode):
    """
    Resolution of three body axis accelerations to compute the lateral
    acceleration, that is, in the direction perpendicular to the aircraft
    centreline when projected onto the earth's surface. Right = +ve.
    """

    units = 'g'

    def derive(self, acc_norm=P('Acceleration Normal Offset Removed'),
               acc_lat=P('Acceleration Lateral Offset Removed'),
               acc_long=P('Acceleration Longitudinal'),
               pitch=P('Pitch'), roll=P('Roll')):
        pitch_rad = pitch.array * deg2rad
        roll_rad = roll.array * deg2rad
        # Simple Numpy algorithm working on masked arrays
        resolved_in_pitch = (acc_long.array * np.ma.sin(pitch_rad)
                             + acc_norm.array * np.ma.cos(pitch_rad))
        self.array = (resolved_in_pitch * np.ma.sin(roll_rad)
                      + acc_lat.array * np.ma.cos(roll_rad))


class AirspeedForFlightPhases(DerivedParameterNode):

    units = 'kts'

    def derive(self, airspeed=P('Airspeed')):
        self.array = hysteresis(
            repair_mask(airspeed.array, repair_duration=None,
                        zero_if_masked=True), HYSTERESIS_FPIAS)


################################################################################
# Airspeed Minus V2 (Airspeed relative to V2 or a fixed value.)


# TODO: Ensure that this derived parameter supports fixed values.
class AirspeedMinusV2(DerivedParameterNode):
    '''
    Airspeed on takeoff relative to:

    - V2    -- Airbus, Boeing, or any other aircraft that has V2.
    - Fixed -- Prop aircraft, or as required.

    A fixed value will most likely be zero making this relative airspeed
    derived parameter the same as the original absolute airspeed parameter.
    '''
    @classmethod
    def can_operate(cls, available):
        return ('Airspeed' in available and ('V2' in available or 'V2 Lookup' in available))

    units = 'kts'

    def derive(self, airspeed=P('Airspeed'),
               v2_recorded=P('V2'),
               v2_lookup=P('V2 Lookup')):
        '''
        Where V2 is recorded, a low permitted rate of change of 1.0 kt/sec
        (specified in the Parameter Operating Limit section of the POLARIS
        database) forces all false data to be masked, leaving only the
        required valid data. By repairing the mask with duration = None, the
        valid data is extended. For example, 737-3C data only records V2 on
        the runway and it needs to be extended to permit V-V2 KPVs to be
        recorded during the climbout.
        '''
        
        if v2_recorded:
            v2 = v2_recorded
        else:
            v2 = v2_lookup
        # If the data starts in mid-flight, there may be no valid V2 values.
        if np.ma.count(v2.array):
            repaired_v2 = repair_mask(v2.array,
                                      copy=True,
                                      repair_duration=None,
                                      extrapolate=True)
            self.array = airspeed.array - repaired_v2
        else:
            self.array = np_ma_zeros_like(airspeed.array)

            #param.invalid = 1
            #param.array.mask = True
            #hdf.set_param(param, save_data=False, save_mask=True)
            #logger.info("Marked param '%s' as invalid as ptp %.2f "\
                        #"did not exceed minimum change %.2f",
                        #ptp, min_change)


class AirspeedMinusV2For3Sec(DerivedParameterNode):
    '''
    Airspeed on takeoff relative to V2 over a 3 second window.

    See the derived parameter 'Airspeed Minus V2'.
    '''

    units = 'kts'
    
    align_frequency = 2
    align_offset = 0

    def derive(self, spd_v2=P('Airspeed Minus V2')):
        '''
        '''
        self.array = second_window(spd_v2.array, self.frequency, 3)
        #self.array = clip(spd_v2.array, 3.0, spd_v2.frequency)


################################################################################
# Airspeed Minus Min Manoeuver

class AirspeedMinusMinManeouvringSpeed(DerivedParameterNode):
    '''
    Airspeed compared with Airspeed Manoeuver Minimum.
    '''

    units = 'kts'

    def derive(self, airspeed=P('Airspeed'),
               manoeuver_min=P('Min Maneouvring Speed')):
        '''
        '''
        self.array = airspeed.array - manoeuver_min.array


################################################################################
# Airspeed Relative (Airspeed relative to Vapp, Vref or a fixed value.)


class AirspeedReference(DerivedParameterNode):
    '''
    Airspeed on approach will use recorded value if present. If no recorded
    value AFR values will be used.

    Achieved flight records without a recorded value will be repeated
    thoughout the approach sections of the flight.

    - Vapp  -- Airbus
    - Vref  -- Boeing
    - Fixed -- Prop aircraft, or as required.

    A fixed value will most likely be zero making this relative airspeed
    derived parameter the same as the original absolute airspeed parameter.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        vapp = 'Vapp' in available
        vref = 'Vref' in available
        afr = all_of(('Airspeed', 'Approach And Landing'), available) and any_of(['AFR Vapp', 'AFR Vref'], available)
        return vapp or vref or afr

    def derive(self,
               air_spd=P('Airspeed'),
               vapp=P('Vapp'),
               vref=P('Vref'),
               afr_vapp=A('AFR Vapp'),
               afr_vref=A('AFR Vref'),
               approaches=S('Approach And Landing')):

        if vapp:
            # Use recorded Vapp parameter:
            self.array = vapp.array
        elif vref:
            # Use recorded Vref parameter:
            self.array = vref.array
        else:
            # Use provided Vapp/Vref from achieved flight record:
            afr_vspeed = afr_vapp or afr_vref
            self.array = np.ma.zeros(len(air_spd.array), np.double)
            self.array.mask = True
            for approach in approaches:
                self.array[approach.slice] = afr_vspeed.value


class AirspeedReferenceLookup(DerivedParameterNode):
    '''
    Airspeed on approach lookup based on weight and Flap (Surface detents) at
    landing will be used.

    Flap is used as first dependant to avoid interpolation of Flap detents when
    Flap is recorded at a lower frequency than Airspeed.

    if approach leads to touchdown use max flap/conf recorded in approach phase.
    if approach does not lead to touchdown use max flaps recorded in approach phase
    if flap/conf not in lookup table use max flaps setting
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family'),
                    engine=A('Engine Series'), engine_type=A('Engine Type')):

        try:
            cls._get_vspeed_class(series, family, engine,
                                  engine_type)().tables['vref']
        except KeyError:
            return False

        x = set(available)
        base = ['Airspeed', 'Approach And Landing', 'Touchdown']
        weight = base + ['Gross Weight Smoothed']
        airbus = set(weight + ['Configuration']).issubset(x)
        boeing = set(weight + ['Flap']).issubset(x)
        #propeller = set(base + ['Eng (*) Np Avg']).issubset(x)
        # FIXME: Replace the flaky logic for small propeller aircraft which do
        #        not record gross weight, cannot provide achieved flight
        #        records and will be using a fixed value for processing.
        return (airbus or boeing) # or propeller

    @staticmethod
    def _get_vspeed_class(series, family, engine, engine_type):
        x = map(lambda x: x.value if x else None,
                (series, family, engine, engine_type))
        return get_vspeed_map(*x)

    def derive(self,
               flap=M('Flap'),
               conf=P('Configuration'),
               air_spd=P('Airspeed'),
               gw=P('Gross Weight Smoothed'),
               approaches=S('Approach And Landing'),
               touchdowns=KTI('Touchdown'),
               series=A('Series'),
               family=A('Family'),
               engine=A('Engine Series'),
               engine_type=A('Engine Type'),
               spd_ref=P('Airspeed Reference'),
               eng_np=P('Eng (*) Np Avg')):
        '''
        Raises KeyError if no entries for Family/Series in vspeed lookup map.
        '''

        self.array = np_ma_masked_zeros_like(air_spd.array)

        x = map(lambda x: x.value if x else None, (series, family, engine, engine_type))
        try:
            vspeed_class = get_vspeed_map(*x)
        except KeyError as err:
            if spd_ref:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            return

        if gw is not None:  # and you must have eng_np
            try:
                # Allow up to 2 superframe values to be repaired:
                # (64 * 2 = 128 + a bit)
                repaired_gw = repair_mask(gw.array, repair_duration=130,
                                          copy=True, extrapolate=True)
            except:
                self.warning("'Airspeed Reference' will be fully masked "
                    "because 'Gross Weight Smoothed' array could not be "
                    "repaired.")
                return

        setting_param = conf or flap # check Conf as is dependant on Flap
        vspeed_table = vspeed_class()
        for approach in approaches:
            _slice = approach.slice
            index, setting = max_value(setting_param.array, _slice)
            # Allow no gross weight for aircraft which use a fixed vspeed
            weight = repaired_gw[index] if gw is not None else None

            if not is_index_within_slice(touchdowns.get_last().index, _slice) \
                and setting not in vspeed_table.vref_settings:
                # Not the final landing and max setting not in vspeed table,
                # so use the maximum setting possible as a reference.
                if setting_param.name == 'Flap':
                    max_setting = max(get_flap_map(series.value, family.value))
                else:
                    max_setting = max(get_conf_map(series.value, family.value).keys())
                self.info("No touchdown in this approach and maximum "
                          "%s '%s' not in lookup table. Using max "
                          "possible setting '%s' as reference",
                          setting_param.name, setting, max_setting)
                setting = max_setting
            else:
                # We either touched down, so use the touchdown flap/conf
                # setting or we had reached a maximum flap setting during the
                # approach which in the vref table. Continue to establish Vref.
                pass

            try:
                vspeed = vspeed_table.vref(setting, weight)
            except  (KeyError, ValueError) as err:
                if spd_ref:
                    self.info("Error in '%s': %s", self.name, err)
                else:
                    self.warning("Error in '%s': %s", self.name, err)
                # Where the aircraft takes off with flap settings outside the
                # documented vref range, we need the program to continue without
                # raising an exception, so that the incorrect flap at landing
                # can be detected.
            else:
                if vspeed is not None:
                    self.array[_slice] = vspeed


class AirspeedRelative(DerivedParameterNode):
    '''
    Airspeed minus Vref/Vapp if recorded or supplied by AFR records. Falls
    back to lookup tables if these do not exist.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and \
               any_of(('Airspeed Reference','Airspeed Reference Lookup'), available)

    def derive(self, airspeed=P('Airspeed'),
               vref_recorded=P('Airspeed Reference'),
               vref_lookup=P('Airspeed Reference Lookup')):

        if vref_recorded:
            vref = vref_recorded
        else:
            vref = vref_lookup

        self.array = airspeed.array - vref.array


class AirspeedRelativeFor3Sec(DerivedParameterNode):
    '''
    Airspeed relative to Vapp/Vref over a 3 second window.

    See the derived parameter 'Airspeed Relative'.
    '''

    units = 'kts'
    align_frequency = 2
    align_offset = 0

    def derive(self, spd_vref=P('Airspeed Relative')):
        '''
        '''
        self.array = second_window(spd_vref.array, self.frequency, 3)


################################################################################


class AirspeedTrue(DerivedParameterNode):
    """
    True airspeed is computed from the recorded airspeed and pressure
    altitude. We assume that the recorded airspeed is indicated or computed,
    and that the pressure altitude is on standard (1013mB = 29.92 inHg).

    There are a few aircraft still operating which do not record the air
    temperature, so only these two parameters are required for the algorithm
    to run.

    Where air temperature is available, we accept Static Air Temperature
    (SAT) and include this accordingly. If TAT is recorded, it will have
    already been converted by the SAT derive function.

    True airspeed is also extended to the ends of the takeoff and landing
    run, in particular so that we can estimate the minimum airspeed at which
    thrust reversers are used.

    -------------------------------------------------------------------------
    Thanks are due to Kevin Horton of Ottawa for permission to derive the
    code here from his AeroCalc library.
    -------------------------------------------------------------------------
    """

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        return 'Airspeed' in available and 'Altitude STD' in available

    def derive(self, cas_p=P('Airspeed'), alt_std_p=P('Altitude STD'),
               sat_p=P('SAT'), toffs=S('Takeoff'), lands=S('Landing'),
               gspd=P('Groundspeed'), acc_fwd=P('Acceleration Forwards')):

        cas = cas_p.array
        alt_std = alt_std_p.array
        dp = cas2dp(cas)
        if sat_p:
            sat = sat_p.array
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(
                np.logical_or(np.ma.getmaskarray(cas_p.array),
                              np.ma.getmaskarray(alt_std_p.array)),
                np.ma.getmaskarray(sat_p.array))
        else:
            sat = alt2sat(alt_std)
            tas = dp2tas(dp, alt_std, sat)
            combined_mask= np.logical_or(cas_p.array.mask,alt_std_p.array.mask)

        tas_from_airspeed = np.ma.masked_less(
            np.ma.array(data=tas, mask=combined_mask), 50)
        tas_valids = np.ma.clump_unmasked(tas_from_airspeed)

        if all([gspd, toffs, lands]):
            # Now see if we can extend this during the takeoff phase, using
            # either recorded groundspeed or failing that integrating
            # acceleration:
            for toff in toffs:
                for tas_valid in tas_valids:
                    tix = tas_valid.start
                    if is_index_within_slice(tix, toff.slice):
                        tas_0 = tas_from_airspeed[tix]
                        wind = tas_0 - gspd.array[tix]
                        scope = slice(toff.slice.start, tix)
                        if gspd:
                            tas_from_airspeed[scope] = gspd.array[scope] + wind
                        else:
                            tas_from_airspeed[scope] = \
                                integrate(acc_fwd.array[scope],
                                          acc_fwd.frequency,
                                          initial_value=tas_0,
                                          scale=GRAVITY_IMPERIAL / KTS_TO_FPS,
                                          direction='backwards')

            # Then see if we can do the same for the landing phase:
            for land in lands:
                for tas_valid in tas_valids:
                    tix = tas_valid.stop - 1
                    if is_index_within_slice(tix, land.slice):
                        tas_0 = tas_from_airspeed[tix]
                        wind = tas_0 - gspd.array[tix]
                        scope = slice(tix + 1, land.slice.stop)
                        if gspd:
                            tas_from_airspeed[scope] = gspd.array[scope] + wind
                        else:
                            tas_from_airspeed[scope] = \
                                integrate(acc_fwd.array[scope],
                                          acc_fwd.frequency,
                                          initial_value=tas_0,
                                          scale=GRAVITY_IMPERIAL / KTS_TO_FPS)

        self.array = tas_from_airspeed


class AltitudeAAL(DerivedParameterNode):
    """
    This is the main altitude measure used during flight analysis.

    Where radio altimeter data is available, this is used for altitudes up to
    100ft and thereafter the pressure altitude signal is used. The two are
    "joined" together at the sample above 100ft in the climb or descent as
    appropriate.

    If no radio altitude signal is available, the simple measure based on
    pressure altitude only is used, which provides workable solutions except
    that the point of takeoff and landing may be inaccurate.

    This parameter includes a rejection of bounced landings of less than 35ft
    height.
    """
    name = "Altitude AAL"
    units = 'ft'
    align_frequency = 2 
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return 'Altitude STD Smoothed' in available and 'Fast' in available

    def compute_aal(self, mode, alt_std, low_hb, high_gnd, alt_rad=None):

        alt_result = np_ma_zeros_like(alt_std)

        def shift_alt_std():
            '''
            Return Altitude STD Smoothed shifted relative to 0 for cases where we do not
            have a reliable Altitude Radio.
            '''
            try:
                # Test case => NAX_8_LN-NOE_20120109063858_02_L3UQAR___dev__sdb.002.hdf5
                # Look over the first 500ft of climb (or less if the data doesn't get that high).
                to = index_at_value(alt_std, min(alt_std[0]+500, np.ma.max(alt_std)))
                # Seek the point where the altitude first curves upwards.
                idx = int(peak_curvature(repair_mask(alt_std[:to]),
                                         curve_sense='Concave',
                                         gap = 7,
                                         ttp = 10))
                # The liftoff most probably arose in the preceding 10
                # seconds. Allow 3 seconds afterwards for luck.
                rotate = slice(max(idx-10*self.frequency,0),
                               idx+3*self.frequency)
                # Draw a straight line across this period with a ruler.
                p,m,c = coreg(alt_std[rotate])
                ruler = np.ma.arange(rotate.stop-rotate.start)*m+c
                # Measure how far the altitude is below the ruler.
                delta = alt_std[rotate] - ruler
                # The liftoff occurs where the gap is biggest because this is
                # where the wing lift has caused the local pressure to
                # increase, hence the altitude appears to decrease.
                pit = alt_std[np.ma.argmin(delta)+rotate.start]
                
                '''
                # Quick visual check of the operation of the takeoff point detection.
                import matplotlib.pyplot as plt
                plt.plot(alt_std[:to])
                xnew = np.linspace(rotate.start,rotate.stop,num=2)
                ynew = (xnew-rotate.start)*m + c
                plt.plot(xnew,ynew,'-')                
                plt.plot(np.ma.argmin(delta)+rotate.start, pit, 'dg')
                plt.plot(idx, alt_std[idx], 'dr')
                plt.show()
                plt.clf()
                '''

            except:
                # If something odd about the data causes a problem with this
                # technique, use a simpler solution. This can give
                # significantly erroneous results in the case of sloping
                # runways, but it's the most robust technique.
                pit = np.ma.min(alt_std)
            alt_result = alt_std - pit
            return np.ma.maximum(alt_result, 0.0)

        if alt_rad is None or np.ma.count(alt_rad)==0:
            # This backstop trap for negative values is necessary as aircraft
            # without rad alts will indicate negative altitudes as they land.
            if mode != 'land':
                return alt_std - high_gnd
            else:
                return shift_alt_std()

        if mode=='over_gnd' and (low_hb-high_gnd)>100.0:
            return alt_std - high_gnd
        

        # We pretend the aircraft can't go below ground level for altitude AAL:
        alt_rad_aal = np.ma.maximum(alt_rad, 0.0)
        ralt_sections = np.ma.clump_unmasked(np.ma.masked_outside(alt_rad_aal, 0.1, 100.0))
        if len(ralt_sections)==0:
            # Either Altitude Radio did not drop below 100, or did not get
            # above 100. Either way, we are better off working with just the
            # pressure altitude signal.
            return shift_alt_std()

        if mode=='land':
            # We refine our definition of the radio altimeter sections to
            # take account of bounced landings and altimeters which read
            # small positive values on the ground.
            bounce_sections = [y for y in ralt_sections if np.ma.max(alt_rad[y]>BOUNCED_LANDING_THRESHOLD)]
            bounce_end = bounce_sections [0].start
            hundred_feet = bounce_sections [-1].stop
        
            alt_result[bounce_end:hundred_feet] = alt_rad_aal[bounce_end:hundred_feet]
            alt_result[:bounce_end] = 0.0
            ralt_sections = [slice(0,hundred_feet)]

        baro_sections = slices_not(ralt_sections, begin_at=0, 
                                   end_at=len(alt_std))

        for ralt_section in ralt_sections:
            if np.ma.mean(alt_std[ralt_section] - alt_rad_aal[ralt_section]) > 10000:
                # Difference between Altitude STD and Altitude Radio should not
                # be greater than 10000 ft when Altitude Radio is recording below
                # 100 ft. This will not fix cases when Altitude Radio records
                # spurious data at lower altitudes.
                continue
            alt_result[ralt_section] = alt_rad_aal[ralt_section]

            for baro_section in baro_sections:
                # I know there must be a better way to code these symmetrical processes, but this works :o)
                link_baro_rad_fwd(baro_section, ralt_section, alt_rad_aal, alt_std, alt_result)
                link_baro_rad_rev(baro_section, ralt_section, alt_rad_aal, alt_std, alt_result)

        return alt_result

    def derive(self, alt_rad=P('Altitude Radio'),
               alt_std=P('Altitude STD Smoothed'),
               speedies=S('Fast')):
        # Altitude Radio taken as the prime reference to ensure the minimum
        # ground clearance passing peaks is accurately reflected. Alt AAL
        # forced to 2htz

        # alt_aal will be zero on the airfield, so initialise to zero.
        alt_aal = np_ma_zeros_like(alt_std.array)

        for speedy in speedies:
            quick = speedy.slice
            if speedy.slice == slice(None, None, None):
                self.array = alt_aal
                break

            # We set the minimum height for detecting flights to 500 ft. This
            # ensures that low altitude "hops" are still treated as complete
            # flights while more complex flights are processed as climbs and
            # descents of 500 ft or more.
            alt_idxs, alt_vals = cycle_finder(alt_std.array[quick],
                                              min_step=500)

            # Reference to start of arrays for simplicity hereafter.
            if alt_idxs == None:
                continue

            alt_idxs += quick.start or 0

            n = 0
            dips = []
            # List of dicts, with each sublist containing:

            # 'type' of item 'land' or 'over_gnd' or 'high'

            # 'slice' for this part of the data
            # if 'type' is 'land' the land section comes at the beginning of the
            # slice (i.e. takeoff slices are normal, landing slices are
            # reversed)
            # 'over_gnd' or 'air' are normal slices.

            # 'alt_std' as:
            # 'land' = the pressure altitude on the ground
            # 'over_gnd' = the pressure altitude when flying closest to the
            #              ground
            # 'air' = the lowest pressure altitude in this slice

            # 'highest_ground' in this area
            # 'land' = the pressure altitude on the ground
            # 'over_gnd' = the pressure altitude minus the radio altitude when
            #              flying closest to the ground
            # 'air' = None (the aircraft was too high for the radio altimeter to
            #         register valid data

            n_vals = len(alt_vals)
            while n < n_vals - 1:
                alt = alt_vals[n]
                alt_idx = alt_idxs[n]
                next_alt = alt_vals[n + 1]
                next_alt_idx = alt_idxs[n + 1]

                if next_alt > alt:
                    # Rising section.
                    dips.append({
                        'type': 'land',
                        'slice': slice(quick.start, next_alt_idx),
                        # was 'slice': slice(alt_idx, next_alt_idx),
                        'alt_std': alt,
                        'highest_ground': alt,
                    })
                    n += 1
                    continue

                if n + 2 >= n_vals:
                    # Falling section. Slice it backwards to use the same code
                    # as for takeoffs.
                    dips.append({
                        'type': 'land',
                        'slice': slice(quick.stop, alt_idx - 1, -1),
                        # was 'slice': slice(next_alt_idx - 1, alt_idx - 1, -1),
                        'alt_std': next_alt,
                        'highest_ground': next_alt,
                    })
                    n += 1
                    continue

                if alt_vals[n + 2] > next_alt:
                    # A down and up section.
                    down_up = slice(alt_idx, alt_idxs[n + 2])
                    # Is radio altimeter data both supplied and valid in this
                    # range?
                    if alt_rad and np.ma.count(alt_rad.array[down_up]) > 0:
                        # Let's find the lowest rad alt reading
                        # (this may not be exactly the highest ground, but
                        # it was probably the point of highest concern!)
                        arg_hg_max = \
                            np.ma.argmin(alt_rad.array[down_up]) + \
                            alt_idxs[n]
                        hg_max = alt_std.array[arg_hg_max] - \
                            alt_rad.array[arg_hg_max]
                        if np.ma.count(hg_max):
                            # The rad alt measured height above a peak...
                            dips.append({
                                'type': 'over_gnd',
                                'slice': down_up,
                                'alt_std': alt_std.array[arg_hg_max],
                                'highest_ground': hg_max,
                            })
                    else:
                        # We have no rad alt data we can use.
                        # TODO: alt_std code needs careful checking.
                        if dips:
                            prev_dip = dips[-1]
                        if dips and prev_dip['type'] == 'high':
                            # Join this dip onto the previous one
                            prev_dip['slice'] = \
                                slice(prev_dip['slice'].start,
                                      alt_idxs[n + 2])
                            prev_dip['alt_std'] = \
                                min(prev_dip['alt_std'],
                                    next_alt)
                        else:
                            dips.append({
                                'type': 'high',
                                'slice': down_up,
                                'alt_std': next_alt,
                                'highest_ground': next_alt,
                            })
                    n += 2
                else:
                    raise ValueError('Problem in Altitude AAL where data '
                                     'should dip, but instead has a peak.')

            for n, dip in enumerate(dips):
                if dip['type'] == 'high':
                    if n == 0:
                        if len(dips) == 1:
                            # Arbitrary offset in indeterminate case.
                            dip['alt_std'] = dip['highest_ground']+1000.0
                        else:
                            next_dip = dips[n + 1]
                            dip['highest_ground'] = \
                                dip['alt_std'] - next_dip['alt_std'] + \
                                next_dip['highest_ground']
                    elif n == len(dips) - 1:
                        prev_dip = dips[n - 1]
                        dip['highest_ground'] = \
                            dip['alt_std'] - prev_dip['alt_std'] + \
                            prev_dip['highest_ground']
                    else:
                        # Here is the most commonly used, and somewhat
                        # arbitrary code. For a dip where no radio
                        # measurement of the ground is available, what height
                        # can you use as the datum? The lowest ground
                        # elevation in the preceding and following sections
                        # is practical, a little optimistic perhaps, but
                        # useable until we find a case otherwise.
                        
                        # This was modified to ensure the minimum height was
                        # 1000ft as we had a case where the lowest dips were
                        # below the takeoff and landing airfields.
                        next_dip = dips[n + 1]
                        prev_dip = dips[n - 1]
                        dip['highest_ground'] = min(prev_dip['highest_ground'],
                                                    dip['alt_std']-1000.0,
                                                    next_dip['highest_ground'])

            for dip in dips:
                alt_rad_section = alt_rad.array[dip['slice']] if alt_rad else None
                alt_aal[dip['slice']] = self.compute_aal(
                    dip['type'],
                    alt_std.array[dip['slice']],
                    dip['alt_std'],
                    dip['highest_ground'],
                    alt_rad=alt_rad_section)
            
            # Reset end sections
            alt_aal[quick.start:alt_idxs[0]+1] = 0.0
            alt_aal[alt_idxs[-1]+1:quick.stop] = 0.0
        
        '''
        # Quick visual check of the altitude aal.
        if alt_rad:
            import matplotlib.pyplot as plt
            plt.plot(alt_aal)
            plt.plot(alt_std.array)
            plt.plot(alt_rad.array)
            plt.show()
        '''
        
        self.array = alt_aal


def link_baro_rad_fwd(baro_section, ralt_section, alt_rad, alt_std, alt_result):
    begin_index = baro_section.start

    if ralt_section.stop == begin_index:
        start_plus_60 = min(begin_index + 60, len(alt_std))
        alt_diff = (alt_std[begin_index:start_plus_60] -
                    alt_rad[begin_index:start_plus_60])
        slip, up_diff = first_valid_sample(alt_diff)
        if slip is None:
            up_diff = 0.0
        else:
            # alt_std is invalid at the point of handover
            # so stretch the radio signal until we can
            # handover.
            fix_slice = slice(begin_index,
                              begin_index + slip)
            alt_result[fix_slice] = alt_rad[fix_slice]
            begin_index += slip

        alt_result[begin_index:] = \
            alt_std[begin_index:] - up_diff

def link_baro_rad_rev(baro_section, ralt_section, alt_rad, alt_std, alt_result):
    end_index = baro_section.stop

    if ralt_section.start == end_index:
        end_minus_60 = max(end_index-60, 0)
        alt_diff = (alt_std[end_minus_60:end_index] -
                    alt_rad[end_minus_60:end_index])
        slip, up_diff = first_valid_sample(alt_diff[::-1])
        if slip is None:
            up_diff = 0.0
        else:
            # alt_std is invalid at the point of handover
            # so stretch the radio signal until we can
            # handover.
            fix_slice = slice(end_index-slip,
                              end_index)
            alt_result[fix_slice] = alt_rad[fix_slice]
            end_index -= slip

        alt_result[:end_index] = \
            alt_std[:end_index] - up_diff


class AltitudeAALForFlightPhases(DerivedParameterNode):
    name = 'Altitude AAL For Flight Phases'
    units = 'ft'

    # This parameter repairs short periods of masked data, making it suitable
    # for detecting altitude bands on the climb and descent. The parameter
    # should not be used to compute KPV values themselves, to avoid using
    # interpolated values in an event.

    def derive(self, alt_aal=P('Altitude AAL')):
        self.array = repair_mask(alt_aal.array, repair_duration=None)


class AltitudeRadio(DerivedParameterNode):
    """
    There is a wide variety of radio altimeter installations with one, two or
    three sensors recorded - each with different timing, sample rate and
    inaccuracies to be compensated. This derive process gathers all the
    available data and passes the blending task to blend_parameters where
    multiple cubic splines are joined with variable weighting to provide an
    optimal combination of the available data.

    :returns Altitude Radio with values typically taken as the mean between
    two valid sensors.
    :type parameter object.
    """

    units = 'ft'
    align = False

    @classmethod
    def can_operate(cls, available):
        return any_of([name for name in cls.get_dependency_names() \
                       if name.startswith('Altitude Radio')], available)

    def derive(self,
               source_A = P('Altitude Radio (A)'),
               source_B = P('Altitude Radio (B)'),
               source_C = P('Altitude Radio (C)'),
               source_L = P('Altitude Radio (L)'),
               source_R = P('Altitude Radio (R)'),
               source_efis = P('Altitude Radio (EFIS)'),
               source_efis_L = P('Altitude Radio (EFIS) (L)'),
               source_efis_R = P('Altitude Radio (EFIS) (R)')):
        
        # Reminder: If you add parameters here, they need limits adding in the database !!!
        
        sources = [source_A, source_B, source_C, source_L, source_R,
                   source_efis, source_efis_L, source_efis_R]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources,
                                      offset=self.offset, 
                                      frequency=self.frequency)


class AltitudeSTDSmoothed(DerivedParameterNode):
    """
    :param frame: The frame attribute, e.g. '737-i'
    :type frame: An attribute

    :returns Altitude STD Smoothed as a local average where the original source is unacceptable, but unchanged otherwise.
    :type parameter object.
    """

    name = "Altitude STD Smoothed"
    units = 'ft'
    align=False

    @classmethod
    def can_operate(cls, available):
        return 'Altitude STD' in available

    def derive(self, fine = P('Altitude STD (Fine)'), 
               alt = P('Altitude STD'),
               alt_capt = P('Altitude STD (Capt)'), 
               alt_fo = P('Altitude STD (FO)'),
               frame = A('Frame')):

        frame_name = frame.value if frame else ''

        if frame_name in ['737-i', '757-DHL', '767-3A'] or \
           frame_name.startswith('737-6'):
            # The altitude signal is measured in steps of 32 ft (10ft for
            # 757-DHL) so needs smoothing. A 5-point Gaussian distribution
            # was selected as a balance between smoothing effectiveness and
            # excessive manipulation of the data.
            gauss = [0.054488683, 0.244201343, 0.402619948, 0.244201343, 0.054488683]
            self.array = moving_average(alt.array, window=5, weightings=gauss)
            
        elif frame_name in ['E135-145', 'L382-Hercules']:
            # Here two sources are sampled alternately, so this form of
            # weighting merges the two to create a smoothed average.
            self.array = moving_average(alt.array, window=3,
                                        weightings=[0.25,0.5,0.25], pad=True)

        elif frame_name.startswith('747-200-') or \
             frame_name in ['A300-203-B4']:
            # The fine recording is used to compute altitude, and here we
            # match the fine part to the coarse part to get the altitudes
            # right.
            self.array = match_altitudes(fine.array, alt.array)

        elif alt_capt and alt_fo:
            # Merge alternate sources if they are available from the LFL
            # Note: The LFL will still need a separate "Altitude STD" parameter
            # to allow the data validation processes to establish flight phases.
            self.array, self.frequency, self.offset = blend_two_parameters(alt_capt, alt_fo)

        else:
            self.array = alt.array

# TODO: Account for 'Touch & Go' - need to adjust QNH for additional airfields!
class AltitudeQNH(DerivedParameterNode):
    '''
    This altitude is above mean sea level. From the takeoff airfield to the
    highest altitude above airfield, the altitude QNH is referenced to the
    takeoff airfield elevation, and from that point onwards it is referenced
    to the landing airfield elevation.

    We can determine the elevation in the following ways:

    1. Take the average elevation between the start and end of the runway.
    2. Take the general elevation of the airfield.

    If we can only determine the takeoff elevation, the landing elevation
    will using the same value as the error will be the difference in pressure
    altitude between the takeoff and landing airports on the day which is
    likely to be less than forcing it to 0. Therefore landing elevation is
    used if the takeoff elevation cannot be determined.

    If we are unable to determine either the takeoff or landing elevations,
    we use the Altitude AAL parameter.
    '''

    name = 'Altitude QNH'
    units = 'ft'

    @classmethod
    def can_operate(cls, available):
        return 'Altitude AAL' in available and 'Altitude Peak' in available

    def derive(self, alt_aal=P('Altitude AAL'), alt_peak=KTI('Altitude Peak'),
            l_apt=A('FDR Landing Airport'), l_rwy=A('FDR Landing Runway'),
            t_apt=A('FDR Takeoff Airport'), t_rwy=A('FDR Takeoff Runway')):
        '''
        We attempt to adjust Altitude AAL by adding elevation at takeoff and
        landing. We need to know the takeoff and landing runway to get the most
        precise elevation, falling back to the airport elevation if they are
        not available.
        '''
        alt_qnh = np.ma.copy(alt_aal.array)  # copy only required for test case

        # Attempt to determine elevation at takeoff:
        t_elev = None
        if t_rwy:
            t_elev = self._calc_rwy_elev(t_rwy.value)
        if t_elev is None and t_apt:
            t_elev = self._calc_apt_elev(t_apt.value)

        # Attempt to determine elevation at landing:
        l_elev = None
        if l_rwy:
            l_elev = self._calc_rwy_elev(l_rwy.value)
        if l_elev is None and l_apt:
            l_elev = self._calc_apt_elev(l_apt.value)

        if t_elev is None and l_elev is None:
            self.warning("No Takeoff or Landing elevation, using Altitude AAL")
            self.array = alt_qnh
            return  # BAIL OUT!
        elif t_elev is None:
            self.warning("No Takeoff elevation, using %dft at Landing", l_elev)
            smooth = False
            t_elev = l_elev
        elif l_elev is None:
            self.warning("No Landing elevation, using %dft at Takeoff", t_elev)
            smooth = False
            l_elev = t_elev
        else:
            # both have valid values
            smooth = True

        # Break the "journey" at the "midpoint" - actually max altitude aal -
        # and be sure to account for rise/fall in the data and stick the peak
        # in the correct half:
        peak = alt_peak.get_first()  # NOTE: Fix for multiple approaches...
        fall = alt_aal.array[peak.index - 1] > alt_aal.array[peak.index + 1]
        peak = peak.index
        if fall:
            peak += int(fall)

        # Add the elevation at takeoff to the climb portion of the array:
        alt_qnh[:peak] += t_elev

        # Add the elevation at landing to the descent portion of the array:
        alt_qnh[peak:] += l_elev

        # Attempt to smooth out any ugly transitions due to differences in
        # pressure so that we don't get horrible bumps in visualisation:
        if smooth:
            # step jump transforms into linear slope
            delta = np.ma.ptp(alt_qnh[peak - 1:peak + 1])
            width = ceil(delta * alt_aal.frequency / 3)
            window = slice(peak - width, peak + width + 1)
            alt_qnh[window] = np.ma.masked
            repair_mask(
                array=alt_qnh,
                repair_duration=window.stop - window.start,
            )

        self.array = alt_qnh

    @staticmethod
    def _calc_apt_elev(apt):
        '''
        '''
        return apt.get('elevation')

    @staticmethod
    def _calc_rwy_elev(rwy):
        '''
        '''
        elev_s = rwy.get('start', {}).get('elevation')
        elev_e = rwy.get('end', {}).get('elevation')
        if elev_s is None:
            return elev_e
        if elev_e is None:
            return elev_s
        # FIXME: Determine based on liftoff/touchdown coordinates?
        return (elev_e + elev_s) / 2


'''
class AltitudeSTD(DerivedParameterNode):
    """
    This section allows for manipulation of the altitude recordings from
    different types of aircraft. Problems often arise due to combination of
    the fine and coarse parts of the data and many different types of
    correction have been developed to cater for these cases.
    """
    name = 'Altitude STD'
    units = 'ft'
    @classmethod
    def can_operate(cls, available):
        high_and_low = 'Altitude STD (Coarse)' in available and \
            'Altitude STD (Fine)' in available
        coarse_and_ivv = 'Altitude STD (Coarse)' in available and \
            'Vertical Speed' in available
        return high_and_low or coarse_and_ivv

    def _high_and_low(self, alt_std_high, alt_std_low, top=18000, bottom=17000):
        # Create empty array to write to.
        alt_std = np.ma.empty(len(alt_std_high.array))
        alt_std.mask = np.ma.mask_or(alt_std_high.array.mask,
                                     alt_std_low.array.mask)
        difference = top - bottom
        # Create average of high and low. Where average is above crossover,
        # source value from alt_std_high. Where average is below crossover,
        # source value from alt_std_low.
        average = (alt_std_high.array + alt_std_low.array) / 2
        source_from_high = average > top
        alt_std[source_from_high] = alt_std_high.array[source_from_high]
        source_from_low = average < bottom
        alt_std[source_from_low] = alt_std_low.array[source_from_low]
        source_from_high_or_low = np.ma.logical_or(source_from_high,
                                                   source_from_low)
        crossover = np.ma.logical_not(source_from_high_or_low)
        crossover_indices = np.ma.where(crossover)[0]
        high_values = alt_std_high.array[crossover]
        low_values = alt_std_low.array[crossover]
        for index, high_value, low_value in zip(crossover_indices,
                                                high_values,
                                                low_values):
            average_value = average[index]
            high_multiplier = (average_value - bottom) / float(difference)
            low_multiplier = abs(1 - high_multiplier)
            crossover_value = (high_value * high_multiplier) + \
                (low_value * low_multiplier)
            alt_std[index] = crossover_value
        return alt_std

    def _coarse_and_ivv(self, alt_std_coarse, ivv):
        alt_std_with_lag = first_order_lag(alt_std_coarse.array, 10,
                                           alt_std_coarse.hz)
        mask = np.ma.mask_or(alt_std_with_lag.mask, ivv.array.mask)
        return np.ma.masked_array(alt_std_with_lag + (ivv.array / 60.0),
                                  mask=mask)

    def derive(self, alt_std_coarse=P('Altitude STD (Coarse)'),
               alt_std_fine=P('Altitude STD (Fine)'),
               ivv=P('Vertical Speed')):
        if alt_std_high and alt_std_low:
            self.array = self._high_and_low(alt_std_coarse, alt_std_fine)
            ##crossover = np.ma.logical_and(average > 17000, average < 18000)
            ##crossover_indices = np.ma.where(crossover)
            ##for crossover_index in crossover_indices:

            ##top = 18000
            ##bottom = 17000
            ##av = (alt_std_high + alt_std_low) / 2
            ##ratio = (top - av) / (top - bottom)
            ##if ratio > 1.0:
                ##ratio = 1.0
            ##elif ratio < 0.0:
                ##ratio = 0.0
            ##alt = alt_std_low * ratio + alt_std_high * (1.0 - ratio)
            ##alt_std  = alt_std * 0.8 + alt * 0.2

            #146-300 945003 (01)
            #-------------------
            ##Set the thresholds for changeover from low to high scales.
            #top = 18000
            #bottom = 17000
            #
            #av = (ALT_STD_HIGH + ALT_STD_LOW) /2
            #ratio = (top - av) / (top - bottom)
            #
            #IF (ratio > 1.0) THEN ratio = 1.0 ENDIF
            #IF (ratio < 0.0) THEN ratio = 0.0 ENDIF
            #
            #alt = ALT_STD_LOW * ratio + ALT_STD_HIGH * (1.0 - ratio)
            #
            ## Smoothing to reduce unsightly noise in the signal. DJ
            #ALT_STDC = ALT_STDC * 0.8 + alt * 0.2
        elif alt_std_coarse and ivv:
            self.array = self._coarse_and_ivv(alt_std_coarse, ivv)
            #ALT_STDC = (last_alt_std * 0.9) + (ALT_STD * 0.1) + (IVVR / 60.0)
            '''


class AltitudeTail(DerivedParameterNode):
    """
    This function allows for the distance between the radio altimeter antenna
    and the point of the airframe closest to tailscrape.

    The parameter gear_to_tail is measured in metres and is the distance from
    the main gear to the point on the tail most likely to scrape the runway.
    """

    units = 'ft'

    #TODO: Review availability of Attribute "Dist Gear To Tail"

    def derive(self, alt_rad=P('Altitude Radio'), pitch=P('Pitch'),
               ground_to_tail=A('Ground To Lowest Point Of Tail'),
               dist_gear_to_tail=A('Main Gear To Lowest Point Of Tail')):
        pitch_rad = pitch.array * deg2rad
        # Now apply the offset
        gear2tail = dist_gear_to_tail.value * METRES_TO_FEET
        ground2tail = ground_to_tail.value * METRES_TO_FEET
        # Prepare to add back in the negative rad alt reading as the aircraft
        # settles on its oleos
        min_rad = np.ma.min(alt_rad.array)
        self.array = (alt_rad.array + ground2tail -
                      np.ma.sin(pitch_rad) * gear2tail - min_rad)


##############################################################################
# Automated Systems



class CabinAltitude(DerivedParameterNode):
    """
    Some aircraft record the cabin altitude in feet, while others record the
    cabin pressure (normally in psi). This function converts the pressure
    reading to altitude equivalent, so that the KPVs can operate only in
    altitude units. After all, the crew set the cabin altitude, not the
    pressure.
    
    Typically aircraft also have the 'Cabin Altitude Warning' discrete parameter.
    """
    units = 'ft'
    
    def derive(self, cp=P('Cabin Press')):
        # assert cp.units=='psi' # Would like to assert units as 'psi'
        self.array = press2alt(cp.array)
    

class ClimbForFlightPhases(DerivedParameterNode):
    """
    This computes climb segments, and resets to zero as soon as the aircraft
    descends. Very useful for measuring climb after an aborted approach etc.
    """

    units = 'ft'

    def derive(self, alt_std=P('Altitude STD Smoothed'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            ups = np.ma.clump_unmasked(np.ma.masked_less(deltas,0.0))
            for up in ups:
                self.array[air.slice][up] = np.ma.cumsum(deltas[up])


class DescendForFlightPhases(DerivedParameterNode):
    """
    This computes descent segments, and resets to zero as soon as the aircraft
    climbs Used for measuring descents, e.g. following a suspected level bust.
    """

    units = 'ft'

    def derive(self, alt_std=P('Altitude STD Smoothed'), airs=S('Fast')):
        self.array = np.ma.zeros(len(alt_std.array))
        repair_mask(alt_std.array) # Remove small sections of corrupt data
        for air in airs:
            deltas = np.ma.ediff1d(alt_std.array[air.slice], to_begin=0.0)
            downs = np.ma.clump_unmasked(np.ma.masked_greater(deltas,0.0))
            for down in downs:
                self.array[air.slice][down] = np.ma.cumsum(deltas[down])


class AOA(DerivedParameterNode):

    align = False
    name = 'AOA'
    units = 'deg'

    def derive(self, aoa_l=P('AOA (L)'), aoa_r=P('AOA (R)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(aoa_l, aoa_r)


class ControlColumn(DerivedParameterNode):
    '''
    The position of the control column blended from the position of the captain
    and first officer's control columns.
    '''
    align = False
    units = 'deg'

    def derive(self,
               posn_capt=P('Control Column (Capt)'),
               posn_fo=P('Control Column (FO)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(posn_capt, posn_fo)


class ControlColumnCapt(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Control Column (Capt)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Control Column (Capt) Potentiometer', 
                       'Control Column (Capt) Synchro'), available)
    
    def derive(self, pot=P('Control Column (Capt) Potentiometer'),
               synchro=P('Control Column (Capt) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array

class ControlColumnFO(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Control Column (FO)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Control Column (FO) Potentiometer', 
                       'Control Column (FO) Synchro'), available)
    
    def derive(self, pot=P('Control Column (FO) Potentiometer'),
               synchro=P('Control Column (FO) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array


class ControlColumnForce(DerivedParameterNode):
    '''
    The combined force from the captain and the first officer.
    '''

    units = 'lbf'

    def derive(self,
               force_capt=P('Control Column Force (Capt)'),
               force_fo=P('Control Column Force (FO)')):
        self.array = force_capt.array + force_fo.array
        # TODO: Check this summation is correct in amplitude and phase.
        # Compare with Boeing charts for the 737NG.


class ControlWheel(DerivedParameterNode):
    '''
    The position of the control wheel blended from the position of the captain
    and first officer's control wheels.
    
    On the ATR42 there is the option of potentiometer or synchro input.
    '''
    align = False
    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return all_of(('Control Wheel (Capt)','Control Wheel (FO)'), available)\
               or\
               any_of(('Control Wheel Synchro','Control Wheel Potentiometer'), available)

    def derive(self,
               posn_capt=P('Control Wheel (Capt)'),
               posn_fo=P('Control Wheel (FO)'),
               synchro=P('Control Wheel Synchro'),
               pot=P('Control Wheel Potentiometer')):

        # Usually we are blending two sensors
        if posn_capt and posn_fo:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(posn_capt, posn_fo)
            return
        # Less commonly we are selecting from a single source
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array


class SidestickAngleCapt(DerivedParameterNode):
    '''
    Angle of the captain's side stick.
    '''
    name = 'Sidestick Angle (Capt)'
    units = 'deg'

    def derive(self,
               pitch_capt=M('Pitch Command (Capt)'),
               roll_capt=M('Roll Command (Capt)')):

        self.array = (pitch_capt.array ** 2 + roll_capt.array ** 2).sqrt()


class SidestickAngleFO(DerivedParameterNode):
    '''
    Angle of the first officer's side stick.
    '''
    name = 'Sidestick Angle (FO)'
    units = 'deg'

    def derive(self,
               pitch_fo=M('Pitch Command (FO)'),
               roll_fo=M('Roll Command (FO)')):

        self.array = (pitch_fo.array ** 2 + roll_fo.array ** 2).sqrt()


class DistanceToLanding(DerivedParameterNode):
    """
    Ground distance to cover before touchdown.

    Note: This parameter gets closer to zero approaching the final touchdown,
    but then increases as the aircraft decelerates on the runway.
    """

    units = 'nm'

    # Q: Is this distance to final landing, or distance to each approach
    # destination (i.e. resets once reaches point of go-around)

    def derive(self, dist=P('Distance Travelled'), tdwns=KTI('Touchdown')):
        if tdwns:
            dist_flown_at_tdwn = dist.array[tdwns.get_last().index]
            self.array = np.ma.abs(dist_flown_at_tdwn - dist.array)
        else:
            self.array = np.zeros_like(dist.array)
            self.array.mask = True


class DistanceTravelled(DerivedParameterNode):
    '''
    Distance travelled in Nautical Miles. Calculated using integral of
    Groundspeed.
    '''

    units = 'nm'

    def derive(self, gspd=P('Groundspeed')):
        self.array = integrate(gspd.array, gspd.frequency, scale=1.0 / 3600.0)


class Drift(DerivedParameterNode):

    align = False
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return (('Drift (1)' in available or 'Drift (2)' in available) or
                ('Track' in available and 'Heading' in available))

    def derive(self, drift_1=P('Drift (1)'), drift_2=P('Drift (2)'),
               track=P('Track'), heading=P('Heading')):
        if drift_1 or drift_2:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(drift_1, drift_2)
        else:
            self.frequency = track.frequency
            self.offset = track.offset
            self.array = track.array - align(heading, track)


################################################################################
# Brakes

class BrakePressure(DerivedParameterNode):
    """
    Gather the recorded brake parameters and convert into a single analogue.

    This node allows for expansion for different types, and possibly
    operation in primary and standby modes.
    """

    align = False

    @classmethod
    def can_operate(cls, available):
        return ('Brake (L) Press' in available and \
                'Brake (R) Press' in available)

    def derive(self, brake_L=P('Brake (L) Press'), brake_R=P('Brake (R) Press')):
        self.array, self.frequency, self.offset = blend_two_parameters(brake_L, brake_R)


################################################################################
# Engine EPR


class Eng_EPRAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Avg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) EPR Min'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) EPR'),
               eng2=P('Eng (2) EPR'),
               eng3=P('Eng (3) EPR'),
               eng4=P('Eng (4) EPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_EPRMinFor5Sec(DerivedParameterNode):
    '''
    Returns the lowest EPR for up to four engines over five seconds.
    '''

    name = 'Eng (*) EPR Min For 5 Sec'
    units = '%'
    align_frequency = 2
    align_offset = 0

    def derive(self,
               eng_epr_min=P('Eng (*) EPR Min')):

        #self.array = clip(eng_epr_min.array, 5.0, eng_epr_min.frequency, remove='troughs')
        self.array = second_window(eng_epr_min.array, self.frequency, 5)


class EngTPRLimitDifference(DerivedParameterNode):
    '''
    '''

    name = 'Eng TPR Limit Difference'

    def derive(self,
               eng_tpr_max=P('Eng (*) TPR Max'),
               eng_tpr_limit=P('Eng TPR Limit Max')):
        
        self.array = eng_tpr_max.array - eng_tpr_limit.array


class Eng_TPRMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) TPR Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) TPR'),
               eng2=P('Eng (2) TPR'),
               eng3=P('Eng (3) TPR'),
               eng4=P('Eng (4) TPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_TPRMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) TPR Min'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) TPR'),
               eng2=P('Eng (2) TPR'),
               eng3=P('Eng (3) TPR'),
               eng4=P('Eng (4) TPR')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Fuel Flow


class Eng_FuelFlow(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Fuel Flow'
    units = 'kg/h'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        # assume all engines Fuel Flow are record at the same frequency
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.sum(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_FuelFlowMin(DerivedParameterNode):
    '''
    The minimum recorded Fuel Flow across all engines.
    
    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Fuel Flow Min'
    units = 'kg/h'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Flow'),
               eng2=P('Eng (2) Fuel Flow'),
               eng3=P('Eng (3) Fuel Flow'),
               eng4=P('Eng (4) Fuel Flow')):
        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)

        
###############################################################################
# Fuel Burn


class Eng_1_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (1) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (1) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_2_FuelBurn(DerivedParameterNode):
    ''''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (2) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (2) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_3_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (3) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (3) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_4_FuelBurn(DerivedParameterNode):
    '''
    Amount of fuel burnt since the start of the data.
    '''

    name = 'Eng (4) Fuel Burn'
    units = 'kg'

    def derive(self,
               ff=P('Eng (4) Fuel Flow')):

        flow = repair_mask(ff.array)
        self.array = np.ma.array(integrate(flow / 3600.0, ff.frequency))


class Eng_FuelBurn(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Fuel Burn'
    units = 'kg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Fuel Burn'),
               eng2=P('Eng (2) Fuel Burn'),
               eng3=P('Eng (3) Fuel Burn'),
               eng4=P('Eng (4) Fuel Burn')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.sum(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Gas Temperature


class Eng_GasTempAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Avg'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_GasTempMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Max'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_GasTempMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Gas Temp Min'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Gas Temp'),
               eng2=P('Eng (2) Gas Temp'),
               eng3=P('Eng (3) Gas Temp'),
               eng4=P('Eng (4) Gas Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine N1


class Eng_N1Avg(DerivedParameterNode):
    '''
    This returns the avaerage N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N1Max(DerivedParameterNode):
    '''
    This returns the highest N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N1Min(DerivedParameterNode):
    '''
    This returns the lowest N1 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N1 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N1'),
               eng2=P('Eng (2) N1'),
               eng3=P('Eng (3) N1'),
               eng4=P('Eng (4) N1')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


class Eng_N1MinFor5Sec(DerivedParameterNode):
    '''
    Returns the lowest N1 for up to four engines over five seconds.
    '''

    name = 'Eng (*) N1 Min For 5 Sec'
    units = '%'
    align_frequency = 2
    align_offset = 0

    def derive(self,
               eng_n1_min=P('Eng (*) N1 Min')):

        #self.array = clip(eng_n1_min.array, 5.0, eng_n1_min.frequency, remove='troughs')
        self.array = second_window(eng_n1_min.array, self.frequency, 5)


################################################################################
# Engine N2


class Eng_N2Avg(DerivedParameterNode):
    '''
    This returns the avaerage N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N2Max(DerivedParameterNode):
    '''
    This returns the highest N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N2Min(DerivedParameterNode):
    '''
    This returns the lowest N2 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N2 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N2'),
               eng2=P('Eng (2) N2'),
               eng3=P('Eng (3) N2'),
               eng4=P('Eng (4) N2')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine N3


class Eng_N3Avg(DerivedParameterNode):
    '''
    This returns the average N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_N3Max(DerivedParameterNode):
    '''
    This returns the highest N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_N3Min(DerivedParameterNode):
    '''
    This returns the lowest N3 in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) N3 Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) N3'),
               eng2=P('Eng (2) N3'),
               eng3=P('Eng (3) N3'),
               eng4=P('Eng (4) N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine Np


class Eng_NpAvg(DerivedParameterNode):
    '''
    This returns the average Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Avg'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)


class Eng_NpMax(DerivedParameterNode):
    '''
    This returns the highest Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Max'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)


class Eng_NpMin(DerivedParameterNode):
    '''
    This returns the lowest Np in any sample period for up to four engines.

    All engines data aligned (using interpolation) and forced the frequency to
    be a higher 4Hz to protect against smoothing of peaks.
    '''

    name = 'Eng (*) Np Min'
    units = '%'
    align_frequency = 4
    align_offset = 0

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Np'),
               eng2=P('Eng (2) Np'),
               eng3=P('Eng (3) Np'),
               eng4=P('Eng (4) Np')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)


################################################################################
# Engine Oil Pressure


class Eng_OilPressAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Avg'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilPressMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Max'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilPressMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Press Min'
    units = 'psi'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Press'),
               eng2=P('Eng (2) Oil Press'),
               eng3=P('Eng (3) Oil Press'),
               eng4=P('Eng (4) Oil Press')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Oil Quantity


class Eng_OilQtyAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Avg'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilQtyMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_OilQtyMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Qty Min'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Qty'),
               eng2=P('Eng (2) Oil Qty'),
               eng3=P('Eng (3) Oil Qty'),
               eng4=P('Eng (4) Oil Qty')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Oil Temperature


class Eng_OilTempAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Avg'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        avg_array = np.ma.average(engines, axis=0)
        if np.ma.count(avg_array) != 0:
            self.array = avg_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(avg_array)


class Eng_OilTempMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Max'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        max_array = np.ma.max(engines, axis=0)
        if np.ma.count(max_array) != 0:
            self.array = max_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(max_array)


class Eng_OilTempMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Oil Temp Min'
    units = 'C'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Oil Temp'),
               eng2=P('Eng (2) Oil Temp'),
               eng3=P('Eng (3) Oil Temp'),
               eng4=P('Eng (4) Oil Temp')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        min_array = np.ma.min(engines, axis=0)
        if np.ma.count(min_array) != 0:
            self.array = min_array
            self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])
        else:
            # Some aircraft have no oil temperature sensors installed, so
            # quit now if there is no valid result.
            self.array = np_ma_masked_zeros_like(min_array)


################################################################################
# Engine Torque


class Eng_TorqueAvg(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Avg'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.average(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_TorqueMax(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Max'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


class Eng_TorqueMin(DerivedParameterNode):
    '''
    '''

    name = 'Eng (*) Torque Min'
    units = 'ft.lb'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Torque'),
               eng2=P('Eng (2) Torque'),
               eng3=P('Eng (3) Torque'),
               eng4=P('Eng (4) Torque')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.min(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Vibration (N1)


class Eng_VibN1Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available first shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N1 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N1'),
               eng2=P('Eng (2) Vib N1'),
               eng3=P('Eng (3) Vib N1'),
               eng4=P('Eng (4) Vib N1'),
               fan1=P('Eng (1) Vib N1 Fan'),
               fan2=P('Eng (2) Vib N1 Fan'),
               lpt1=P('Eng (1) Vib N1 Turbine'),
               lpt2=P('Eng (2) Vib N1 Turbine')):

        engines = vstack_params(eng1, eng2, eng3, eng4, fan1, fan2, lpt1, lpt2)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4, fan1, fan2, lpt1, lpt2])


################################################################################
# Engine Vibration (N2)


class Eng_VibN2Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available second shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N2 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N2'),
               eng2=P('Eng (2) Vib N2'),
               eng3=P('Eng (3) Vib N2'),
               eng4=P('Eng (4) Vib N2'),
               hpc1=P('Eng (1) Vib N2 Compressor'),
               hpc2=P('Eng (2) Vib N2 Compressor'),
               hpt1=P('Eng (1) Vib N2 Turbine'),
               hpt2=P('Eng (2) Vib N2 Turbine')):

        engines = vstack_params(eng1, eng2, eng3, eng4, hpc1, hpc2, hpt1, hpt2)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4, hpc1, hpc2, hpt1, hpt2])


################################################################################
# Engine Vibration (N3)


class Eng_VibN3Max(DerivedParameterNode):
    '''
    This derived parameter condenses all the available third shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib N3 Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib N3'),
               eng2=P('Eng (2) Vib N3'),
               eng3=P('Eng (3) Vib N3'),
               eng4=P('Eng (4) Vib N3')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Vibration (Broadband)


class Eng_VibBroadbandMax(DerivedParameterNode):
    '''
    This derived parameter condenses all the available third shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib Broadband Max'
    align = False

    @classmethod
    def can_operate(cls, available):
        
        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib Broadband'),
               eng2=P('Eng (2) Vib Broadband'),
               eng3=P('Eng (3) Vib Broadband'),
               eng4=P('Eng (4) Vib Broadband'),
               eng1_accel_a=P('Eng (1) Vib Broadband Accel A'),
               eng2_accel_a=P('Eng (2) Vib Broadband Accel A'),
               eng3_accel_a=P('Eng (3) Vib Broadband Accel A'),
               eng4_accel_a=P('Eng (4) Vib Broadband Accel A'),
               eng1_accel_b=P('Eng (1) Vib Broadband Accel B'),
               eng2_accel_b=P('Eng (2) Vib Broadband Accel B'),
               eng3_accel_b=P('Eng (3) Vib Broadband Accel B'),
               eng4_accel_b=P('Eng (4) Vib Broadband Accel B')):
        
        params = (eng1, eng2, eng3, eng4,
                  eng1_accel_a, eng2_accel_a, eng3_accel_a, eng4_accel_a,
                  eng1_accel_b, eng2_accel_b, eng3_accel_b, eng4_accel_b)

        engines = vstack_params(*params)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', params)


################################################################################
# Engine Vibration (A)


class Eng_VibAMax(DerivedParameterNode):
    '''
    This derived parameter condenses all the available first shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib A Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib (A)'),
               eng2=P('Eng (2) Vib (A)'),
               eng3=P('Eng (3) Vib (A)'),
               eng4=P('Eng (4) Vib (A)')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Vibration (B)


class Eng_VibBMax(DerivedParameterNode):
    '''
    This derived parameter condenses all the available second shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib B Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib (B)'),
               eng2=P('Eng (2) Vib (B)'),
               eng3=P('Eng (3) Vib (B)'),
               eng4=P('Eng (4) Vib (B)')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################
# Engine Vibration (C)


class Eng_VibCMax(DerivedParameterNode):
    '''
    This derived parameter condenses all the available third shaft order
    vibration measurements into a single consolidated value.
    '''

    name = 'Eng (*) Vib C Max'
    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               eng1=P('Eng (1) Vib (C)'),
               eng2=P('Eng (2) Vib (C)'),
               eng3=P('Eng (3) Vib (C)'),
               eng4=P('Eng (4) Vib (C)')):

        engines = vstack_params(eng1, eng2, eng3, eng4)
        self.array = np.ma.max(engines, axis=0)
        self.offset = offset_select('mean', [eng1, eng2, eng3, eng4])


################################################################################


class FuelQty(DerivedParameterNode):
    '''
    May be supplanted by an LFL parameter of the same name if available.

    Sum of fuel in left, right and middle tanks where available.
    '''

    align = False

    @classmethod
    def can_operate(cls, available):

        return any_of(cls.get_dependency_names(), available)

    def derive(self,
               fuel_qty1=P('Fuel Qty (1)'),
               fuel_qty2=P('Fuel Qty (2)'),
               fuel_qty3=P('Fuel Qty (3)'),
               fuel_qty_aux=P('Fuel Qty (Aux)')):
        params = []
        for param in (fuel_qty1, fuel_qty2, fuel_qty3, fuel_qty_aux):
            if not param:
                continue
            # Repair array masks to ensure that the summed values are not too small
            # because they do not include masked values.
            try:
                param.array = repair_mask(param.array)
            except ValueError as err:
                # Q: Should we be creating a summed Fuel Qty parameter when
                # omitting a masked parameter? The resulting array will contain
                # values lower than expected. The same problem will occur if
                # a parameter has been marked invalid, though we will not
                # be aware of the problem within a derive method.
                self.warning('Skipping %s while calculating %s: %s. Summed '
                             'fuel quantity may be lower than expected.',
                             param, self, err)
            else:
                params.append(param)

        try:
            stacked_params = vstack_params(*params)
            self.array = np.ma.sum(stacked_params, axis=0)
            self.offset = offset_select('mean', params)
        except:
            # In the case where params are all invalid or empty, return an
            # empty array like the last (inherently recorded) array.
            self.array = np_ma_masked_zeros_like(param.array)
            self.offset = 0.0


################################################################################

## There is no difference between the two sources, and the sample rate is so low as to make merging pointless.
##class GrossWeight(DerivedParameterNode):
    ##'''
    ##Merges alternate gross weight measurements. 757-DHK frame applies.
    ##'''
    ##units = 'kg'
    ##align = False

    ##def derive(self,
               ##source_L = P('Gross Weight (L)'),
               ##source_R = P('Gross Weight (R)'),
               ##frame = A('Frame')):

        ##if frame.value in ['757-DHL']:
            ##self.array, self.frequency, self.offset = \
                ##blend_two_parameters(source_L, source_R)

        ##else:
            ##raise DataFrameError(self.name, frame.value)


class GrossWeightSmoothed(DerivedParameterNode):
    '''
    Gross weight is usually sampled at a low rate and can be very poor in the
    climb, often indicating an increase in weight at takeoff and this effect
    may not end until the aircraft levels in the cruise. Also some aircraft
    weight data saturates at high AUW values, and while the POLARIS Analysis
    Engine can mask this data a subsitute is needed for takeoff weight (hence
    V2) calculations. This can only be provided by extrapolation backwards
    from data available later in the flight.

    This routine uses fuel flow to compute short term changes in weight and
    ties this to the last valid measurement before landing. This is used
    because we need to accurately reflect the landing weight for events
    relating to landing (errors introduced by extrapolating to the takeoff
    weight are less significant). 
    
    We avoid using the recorded fuel weight in this calculation, however it
    is used in the Zero Fuel Weight calculation.
    '''
    units = 'kgs'

    def derive(self, ff=P('Eng (*) Fuel Flow'),
               gw=P('Gross Weight'),
               climbs=S('Climbing'),
               descends=S('Descending'),
               airs=S('Airborne')):

        gw_masked = gw.array.copy()
        gw_masked = mask_inside_slices(gw_masked, climbs.get_slices())
        gw_masked = mask_outside_slices(gw_masked, airs.get_slices())

        gw_nonzero = gw.array.nonzero()[0]
        
        flow = repair_mask(ff.array)
        fuel_to_burn = np.ma.array(integrate(flow / 3600.0, ff.frequency,
                                             direction='reverse'))
        
        try:
            # Find the last point where the two arrays intercept
            valid_index = np.ma.intersect1d(gw_masked.nonzero()[0],
                                            fuel_to_burn.nonzero()[0])[-1]
        except IndexError:
            self.warning(
                "'%s' had no valid samples. Reverting to '%s'.", self.name,
                gw.name)
            self.array = gw.array
            return

        offset = gw_masked[valid_index] - fuel_to_burn[valid_index]

        self.array = fuel_to_burn + offset

        # Test that the resulting array is sensible compared with Gross Weight.
        where_array = np.ma.where(self.array)[0]
        test_index = where_array[len(where_array) / 2]
        #test_index = len(gw_nonzero) / 2
        test_difference = \
            abs(gw.array[test_index] - self.array[test_index]) > 1000
        if test_difference > 1000: # Q: Is 1000 too large?
            raise ValueError(
                "'%s' difference from '%s' at half-way point is greater than "
                "'%s': '%s'." % self.name, gw.name, 1000, test_difference)


class Groundspeed(DerivedParameterNode):
    """
    This caters for cases where some preprocessing is required.
    :param frame: The frame attribute, e.g. '737-i'
    :type frame: An attribute
    :returns groundspeed as the mean between two valid sensors.
    :type parameter object.
    """
    units = 'kts'
    align = False

    @classmethod
    def can_operate(cls, available):
        return all_of(('Altitude STD','Groundspeed (1)','Groundspeed (2)'),
                      available)

    def derive(self,
               alt = P('Altitude STD'),
               source_A = P('Groundspeed (1)'),
               source_B = P('Groundspeed (2)'),
               frame = A('Frame')):

        frame_name = frame.value if frame else ''

        if frame_name in ['757-DHL']:
            # The coding in this frame is unique as it only uses two bits for
            # the first digit of the BCD-encoded groundspeed, limiting the
            # recorded value range to 399 kts. At altitude the aircraft can
            # exceed this so a fiddle is required to sort this out.
            altitude = align(alt, source_A) # Caters for different sample rates.
            adjust_A = np.logical_and(source_A.array<200, altitude>8000).data*400
            source_A.array += adjust_A
            adjust_B = np.logical_and(source_B.array<200, altitude>8000).data*400
            source_B.array += adjust_B
            self.array, self.frequency, self.offset = \
                blend_two_parameters(source_A, source_B)

        else:
            raise DataFrameError(self.name, frame_name)


class FlapAngle(DerivedParameterNode):
    '''
    Gather the recorded flap parameters and convert into a single analogue.
    '''

    align = False
    units = 'deg'

    @classmethod
    def can_operate(cls, available, family=A('Family')):
        flap_angle = any_of((
            'Flap Angle (L)', 'Flap Angle (R)',
            'Flap Angle (L) Inboard', 'Flap Angle (R) Inboard',
        ), available)
        if family and family.value == 'B787':
            return flap_angle and 'Slat Surface' in available
        else:
            return flap_angle
    
    @staticmethod
    def _combine_flap_slat(slat_array, flap_array, conf_map):
        '''
        Combines Flap and Slat parameters and returns a Flap Angle array.
        
        Example conf map (slat, flap):
        'B787': {
            0:    (0, 0),
            1:    (50, 0),
            5:    (50, 5),
            15:   (50, 15),
            20:   (50, 20),
            25:   (100, 20),
            30:   (100, 30),
        }
        Creates interpolation params:
        Slat X: [0, 50, 100]
        Slat Y: [0, 1, 6]
        Flap X: [0, 5, 15, 20, 30]
        Flap Y: [0, 4, 14, 19, 24]
        
        :param slat_array: Slat parameter array.
        :type slat_array: np.ma.masked_array
        :param flap_array: Flap parameter array.
        :type flap_array: np.ma.masked_array
        :param conf_map: Configuration map from model information.
        :type conf_map: {int: (int, int)}
        '''
        # Assumes states are strings.
        previous_state = None
        previous_slat = None
        previous_flap = None
        slat_interp_x = []
        slat_interp_y = []
        flap_interp_x = []
        flap_interp_y = []
        for index, (current_state, (current_slat, current_flap)) in enumerate(sorted(conf_map.items())):
            if index == 0:
                previous_state = current_state
            state_difference = current_state - previous_state
            if index == 0 or (previous_slat != current_slat):
                slat_interp_x.append(current_slat)
                slat_interp_y.append((slat_interp_y[-1] if slat_interp_y else 0)
                                     + state_difference)
                previous_slat = current_slat
            if index == 0 or (previous_flap != current_flap):
                flap_interp_x.append(current_flap)
                flap_interp_y.append((flap_interp_y[-1] if flap_interp_y else 0)
                                     + state_difference)
                previous_flap = current_flap
            previous_state = current_state
        slat_interp = interp1d(slat_interp_x, slat_interp_y)
        flap_interp = interp1d(flap_interp_x, flap_interp_y)
        # Exclude masked values which may be outside of the interpolation range.
        slat_unmasked = np.invert(np.ma.getmaskarray(slat_array))
        flap_unmasked = np.invert(np.ma.getmaskarray(flap_array))
        slat_array[slat_unmasked] = slat_interp(slat_array[slat_unmasked])
        flap_array[flap_unmasked] = flap_interp(flap_array[flap_unmasked])
        return slat_array + flap_array

    def derive(self,
               flap_A=P('Flap Angle (L)'),
               flap_B=P('Flap Angle (R)'),
               flap_A_inboard=P('Flap Angle (L) Inboard'),
               flap_B_inboard=P('Flap Angle (R) Inboard'),
               slat=P('Slat Surface'),
               frame=A('Frame'),
               family=A('Family')):

        frame_name = frame.value if frame else ''
        family_name = family.value if family else ''
        flap_A = flap_A or flap_A_inboard
        flap_B = flap_B or flap_B_inboard
        
        if family_name == 'B787':
            conf_map = get_conf_map(None, family_name)
            # Flap settings 1 and 25 only affect Slat.
            # Combine Flap Angle (L) and Flap Angle (R).
            self.array, self.frequency, self.offset = blend_two_parameters(
                flap_A, flap_B)
            # Frequency will be doubled after blending parameters.
            slat.array = align(slat, self)
            self.array = self._combine_flap_slat(slat.array, self.array,
                                                 conf_map)
        elif frame_name in ['747-200-GE', '747-200-PW', '747-200-AP-BIB']:
            # Only the right inboard flap is instrumented.
            self.array = flap_B.array
        else:
            # By default, blend the two parameters.
            self.array, self.frequency, self.offset = blend_two_parameters(
                flap_A, flap_B)


'''
class SlatSurface(DerivedParameterNode):
    """
    """
    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),

    s1f = M('Slat (1) Fully Extended'),
    s1t = M('Slat (1) In Transit'),
    s1m = M('Slat (1) Mid Extended'),
'''

class SlatSurface(DerivedParameterNode):
    '''
    Combines Slat (L) and Slat (R).
    
    TODO: Reconsider naming of Slat parameters for consistency.
    '''
    @classmethod
    def can_operate(cls, available):
        return 'Slat (L)' in available or 'Slat (R)' in available
    
    def derive(self, slat_l=P('Slat (L)'), slat_r=P('Slat (R)')):
        self.array, self.frequency, self.offset = blend_two_parameters(slat_l,
                                                                       slat_r)


class SlopeToLanding(DerivedParameterNode):
    """
    This parameter was developed as part of the Artificical Intelligence
    analysis of approach profiles, 'Identifying Abnormalities in Aircraft
    Flight Data and Ranking their Impact on the Flight' by Dr Edward Smart,
    Institute of Industrial Research, University of Portsmouth.
    http://eprints.port.ac.uk/4141/
    """
    def derive(self, alt_aal=P('Altitude AAL'), dist=P('Distance To Landing')):
        self.array = alt_aal.array / (dist.array * FEET_PER_NM)




'''

TODO: Revise computation of sliding motion

class GroundspeedAlongTrack(DerivedParameterNode):
    """
    Inertial smoothing provides computation of groundspeed data when the
    recorded groundspeed is unreliable. For example, during sliding motion on
    a runway during deceleration. This is not good enough for long period
    computation, but is an improvement over aircraft where the groundspeed
    data stops at 40kn or thereabouts.
    """
    def derive(self, gndspd=P('Groundspeed'),
               at=P('Acceleration Along Track'),
               alt_aal=P('Altitude AAL'),
               glide = P('ILS Glideslope')):
        at_washout = first_order_washout(at.array, AT_WASHOUT_TC, gndspd.hz,
                                         gain=GROUNDSPEED_LAG_TC*GRAVITY_METRIC)
        self.array = first_order_lag(gndspd.array*KTS_TO_MPS + at_washout,
                                     GROUNDSPEED_LAG_TC,gndspd.hz)


        """
        #-------------------------------------------------------------------
        # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
        # TODO: REMOVE THIS SECTION BEFORE RELEASE
        #-------------------------------------------------------------------
        import csv
        spam = csv.writer(open('beans.csv', 'wb'))
        spam.writerow(['at', 'gndspd', 'at_washout', 'self', 'alt_aal','glide'])
        for showme in range(0, len(at.array)):
            spam.writerow([at.array.data[showme],
                           gndspd.array.data[showme]*KTS_TO_FPS,
                           at_washout[showme],
                           self.array.data[showme],
                           alt_aal.array[showme],glide.array[showme]])
        #-------------------------------------------------------------------
        # TEST OUTPUT TO CSV FILE FOR DEBUGGING ONLY
        # TODO: REMOVE THIS SECTION BEFORE RELEASE
        #-------------------------------------------------------------------
        """
'''

class HeadingContinuous(DerivedParameterNode):
    """
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    (val % 360 in Python) returns the value to display to the user.
    
    Some aircraft have poor matching between captain and first officer
    signals, in which case we supply both parameters and merge here. A single
    "Heading" parameter is also required to allow initial data validation
    processes to recognise flight phases. (CRJ-100-200 is an example).
    """
    units = 'deg'
    align = False
    
    @classmethod
    def can_operate(cls, available):
        return ('Heading' in available or
                all_of(('Heading (Capt)', 'Heading (FO)'), available))
    
    def derive(self, head_mag=P('Heading'),
               head_capt=P('Heading (Capt)'),
               head_fo=P('Heading (FO)')):
        if head_capt and head_fo and (head_capt.hz==head_fo.hz):
            head_capt.array = repair_mask(straighten_headings(head_capt.array))
            head_fo.array = repair_mask(straighten_headings(head_fo.array))
            self.array, self.frequency, self.offset = blend_two_parameters(head_capt, head_fo)
        else:
            self.array = repair_mask(straighten_headings(head_mag.array))


# TODO: Absorb this derived parameter into the 'Holding' flight phase.
class HeadingIncreasing(DerivedParameterNode):
    """
    This parameter is computed to allow holding patterns to be identified. As
    the aircraft can enter a hold turning in one direction, then do a
    teardrop and continue with turns in the opposite direction, we are
    interested in the total angular changes, not the sign of these changes.
    """
    units = 'deg'
    
    def derive(self, head=P('Heading Continuous')):
        rot = np.ma.ediff1d(head.array, to_begin = 0.0)
        self.array = integrate(np.ma.abs(rot), head.frequency)


class HeadingTrueContinuous(DerivedParameterNode):
    '''
    For all internal computing purposes we use this parameter which does not
    jump as it passes through North. To recover the compass display, modulus
    (val % 360 in Python) returns the value to display to the user.
    '''
    units = 'deg'
    
    def derive(self, hdg=P('Heading True')):
        self.array = repair_mask(straighten_headings(hdg.array))


class Heading(DerivedParameterNode):
    """
    Compensates for magnetic variation, which will have been computed
    previously based on the magnetic declanation at the aircraft's location.
    """
    units = 'deg'
    
    def derive(self, head_true=P('Heading True Continuous'),
               mag_var=P('Magnetic Variation')):
        self.array = (head_true.array - mag_var.array) % 360.0


class HeadingTrue(DerivedParameterNode):
    """
    Compensates for magnetic variation, which will have been computed
    previously.
    
    The Magnetic Variation from identified Takeoff and Landing runways is
    taken in preference to that calculated based on geographical latitude and
    longitude in order to account for any compass drift or out of date
    magnetic variation databases on the aircraft.
    """
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return 'Heading Continuous' in available and \
               any_of(('Magnetic Variation From Runway', 'Magnetic Variation'),
                      available)
        
    def derive(self, head=P('Heading Continuous'),
               rwy_var=P('Magnetic Variation From Runway'),
               mag_var=P('Magnetic Variation')):
        if rwy_var and np.ma.count(rwy_var.array):
            # use this in preference
            var = rwy_var.array
        else:
            var = mag_var.array
        self.array = (head.array + var) % 360.0


class ILSFrequency(DerivedParameterNode):
    """
    This code is based upon the normal operation of an Instrument Landing
    System whereby the left and right receivers are tuned to the same runway
    ILS frequency. This allows independent monitoring of the approach by the
    two crew.

    If there is a problem with the system, users can inspect the (1) and (2)
    signals separately, although the normal use will show valid ILS data when
    both are tuned to the same frequency.
    """

    name = "ILS Frequency"
    units='MHz'
    align = False

    @classmethod
    def can_operate(cls, available):
        return ('ILS (1) Frequency' in available and
                'ILS (2) Frequency' in available) or \
               ('ILS-VOR (1) Frequency' in available)
    
    def derive(self, f1=P('ILS (1) Frequency'),f2=P('ILS (2) Frequency'),
               f1v=P('ILS-VOR (1) Frequency'), f2v=P('ILS-VOR (2) Frequency')):

        #TODO: Extend to allow for three-receiver installations


        if f1 and f2:
            first = f1.array
            second = f2.array
        else:
            if f1v and f2v==None:
                # Some aircraft have inoperative ILS-VOR (2) systems, which
                # record frequencies outside the valid range.
                first = f1v.array
            elif f1v and f2v:
                first = f1v.array
                second = f2v.array
            else:
                raise "Unrecognised set of ILS frequency parameters"

        # Mask invalid frequencies
        f1_trim = filter_vor_ils_frequencies(first, 'ILS')
        if f1v and f2v==None:
            mask = first.mask
        else:
            # We look for both
            # receivers being tuned together to form a valid signal
            f2_trim = filter_vor_ils_frequencies(second, 'ILS')
            # and mask where the two receivers are not matched
            mask = np.ma.masked_not_equal(f1_trim - f2_trim, 0.0).mask

        self.array = np.ma.array(data=f1_trim.data, mask=mask)


class ILSLocalizer(DerivedParameterNode):

    """
    This derived parameter merges the available sources into a single
    consolidated parameter. The more complex form of parameter blending is
    used to allow for many permutations.
    """

    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)

    name = "ILS Localizer"
    units = 'dots'
    align = False

    def derive(self,
               source_A=P('ILS (1) Localizer'),
               source_B=P('ILS (2) Localizer'),
               source_C=P('ILS (3) Localizer'),
               
               source_E=P('ILS (L) Localizer'),
               source_F=P('ILS (R) Localizer'),
               source_G=P('ILS (C) Localizer'),
               
               source_J=P('ILS (EFIS) Localizer'),
               ):
        sources = [source_A, source_B, source_C,
                   source_E, source_F, source_G,
                   source_J,
                   ]
        self.offset = 0.0
        self.frequency = 2.0
        self.array = blend_parameters(sources, 
                                      offset=self.offset, 
                                      frequency=self.frequency,
                                      )


class ILSGlideslope(DerivedParameterNode):

    """
    This derived parameter merges the available sources into a single
    consolidated parameter. The more complex form of parameter blending is
    used to allow for many permutations.
    """

    name = "ILS Glideslope"
    units = 'dots'
    align = False

    @classmethod
    def can_operate(cls, available):
        return any_of(cls.get_dependency_names(), available)
    
    def derive(self,
               source_A=P('ILS (1) Glideslope'),
               source_B=P('ILS (2) Glideslope'),
               source_C=P('ILS (3) Glideslope'),
               
               source_E=P('ILS (L) Glideslope'),
               source_F=P('ILS (R) Glideslope'),
               source_G=P('ILS (C) Glideslope'),
               
               source_J=P('ILS (EFIS) Glideslope'),
               ):
        sources = [source_A, source_B, source_C,
                   source_E, source_F, source_G,
                   source_J,
                   ]
        self.offset = 0.0
        self.frequency = 2.0
        self.array = blend_parameters(sources, 
                                      offset=self.offset, 
                                      frequency=self.frequency,
                                      )


class AimingPointRange(DerivedParameterNode):
    """
    Aiming Point Range is derived from the Approach Range. The units are
    converted to nautical miles ready for plotting and the datum is offset to
    either the ILS Glideslope Antenna position where an ILS is installed or
    the nominal threshold position where there is no ILS installation.
    """

    units = 'nm'

    def derive(self, app_rng=P('Approach Range'),
               approaches=App('Approach Information'),
               ):
        self.array = np_ma_masked_zeros_like(app_rng.array)

        for approach in approaches:
            runway = approach.runway
            if not runway:
                # no runway to establish distance to glideslope antenna
                continue
            try:
                extend = runway_distances(runway)[1] # gs_2_loc
            except (KeyError, TypeError):
                extend = runway_length(runway) - 1000 / METRES_TO_FEET

            s = approach.slice
            self.array[s] = (app_rng.array[s] - extend) / METRES_TO_NM


class CoordinatesSmoothed(object):
    '''
    Superclass for SmoothedLatitude and SmoothedLongitude classes as they share
    the adjust_track methods.

    _adjust_track_pp is used for aircraft with precise positioning, usually
    GPS based and qualitatively determined by a recorded track that puts the
    aircraft on the correct runway. In these cases we only apply fine
    adjustment of the approach and landing path using ILS localizer data to
    position the aircraft with respect to the runway centreline.

    _adjust_track_ip is for aircraft with imprecise positioning. In these
    cases we use all the data available to correct for errors in the recorded
    position at takeoff, approach and landing.
    '''
    def taxi_out_track_pp(self, lat, lon, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi out track.
        '''

        lat_out, lon_out, wt = ground_track_precise(lat, lon, speed, hdg,
                                                    freq, 'takeoff')
        return lat_out, lon_out

    def taxi_in_track_pp(self, lat, lon, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi in track.
        '''
        lat_in, lon_in, wt = ground_track_precise(lat, lon, speed, hdg, freq,
                                              'landing')
        return lat_in, lon_in

    def taxi_out_track(self, toff_slice, lat_adj, lon_adj, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi out track.
        TODO: Include lat & lon corrections for precise positioning tracks.
        '''
        lat_out, lon_out = \
            ground_track(lat_adj[toff_slice.start],
                         lon_adj[toff_slice.start],
                         speed[:toff_slice.start],
                         hdg.array[:toff_slice.start],
                         freq,
                         'takeoff')
        return lat_out, lon_out

    def taxi_in_track(self, lat_adj, lon_adj, speed, hdg, freq):
        '''
        Compute a groundspeed and heading based taxi in track.
        '''
        if len(speed):
            lat_in, lon_in = ground_track(lat_adj[0],
                                          lon_adj[0],
                                          speed,
                                          hdg,
                                          freq,
                                          'landing')
            return lat_in, lon_in
        else:
            return [],[]

    def _adjust_track(self, lon, lat, ils_loc, app_range, hdg, gspd, tas,
                      toff, toff_rwy, tdwns, approaches, mobile, precise):
        '''
        Returns track adjustment 
        '''
        # Set up a working space.
        lat_adj = np_ma_masked_zeros_like(hdg.array)
        lon_adj = np_ma_masked_zeros_like(hdg.array)

        mobiles = [s.slice for s in mobile]
        begin = mobiles[0].start
        end = mobiles[-1].stop

        ils_join_offset = None

        #------------------------------------
        # Use synthesized track for takeoffs
        #------------------------------------

        # We compute the ground track using best available data.
        if gspd:
            speed = gspd.array
            freq = gspd.frequency
        else:
            speed = tas.array
            freq = tas.frequency

        try:
            toff_slice = toff[0].slice
        except:
            toff_slice = None

        if toff_slice and precise:
            try:
                lat_out, lon_out = self.taxi_out_track_pp(
                    lat.array[begin:toff_slice.start],
                    lon.array[begin:toff_slice.start],
                    speed[begin:toff_slice.start],
                    hdg.array[begin:toff_slice.start],
                    freq)
            except ValueError:
                self.exception("'%s'. Using non smoothed coordinates for Taxi Out",
                             self.__class__.__name__)
                lat_out = lat.array[begin:toff_slice.start]
                lon_out = lon.array[begin:toff_slice.start]
            lat_adj[begin:toff_slice.start] = lat_out
            lon_adj[begin:toff_slice.start] = lon_out

        elif toff_slice and toff_rwy and toff_rwy.value:

            start_locn_recorded = runway_snap_dict(
                toff_rwy.value,lat.array[toff_slice.start],
                lon.array[toff_slice.start])
            start_locn_default = toff_rwy.value['start']
            _,distance = bearing_and_distance(start_locn_recorded['latitude'],
                                              start_locn_recorded['longitude'],
                                              start_locn_default['latitude'],
                                              start_locn_default['longitude'])

            if distance < 50:
                # We may have a reasonable start location, so let's use that
                start_locn = start_locn_recorded
                initial_displacement = 0.0
            else:
                # The recorded start point is way off, default to 50m down the track.
                start_locn = start_locn_default
                initial_displacement = 50.0
            
            # With imprecise navigation options it is common for the lowest
            # speeds to be masked, so we pretend to accelerate smoothly from
            # standstill.
            if speed[toff_slice][0] is np.ma.masked:
                speed.data[toff_slice][0] = 0.0
                speed.mask[toff_slice][0]=False
                speed[toff_slice] = interpolate(speed[toff_slice])

            # Compute takeoff track from start of runway using integrated
            # groundspeed, down runway centreline to end of takeoff (35ft
            # altitude). An initial value of 100m puts the aircraft at a
            # reasonable position with respect to the runway start.
            rwy_dist = np.ma.array(
                data = integrate(speed[toff_slice], freq,
                                 initial_value=initial_displacement,
                                 scale=KTS_TO_MPS),
                mask = np.ma.getmaskarray(speed[toff_slice]))

            # Similarly the runway bearing is derived from the runway endpoints
            # (this gives better visualisation images than relying upon the
            # nominal runway heading). This is converted to a numpy masked array
            # of the length required to cover the takeoff phase.
            rwy_hdg = runway_heading(toff_rwy.value)
            rwy_brg = np_ma_ones_like(speed[toff_slice])*rwy_hdg

            # The track down the runway centreline is then converted to
            # latitude and longitude.
            lat_adj[toff_slice], lon_adj[toff_slice] = \
                latitudes_and_longitudes(rwy_brg,
                                         rwy_dist,
                                         start_locn)

            lat_out, lon_out = self.taxi_out_track(toff_slice, lat_adj, lon_adj, speed, hdg, freq)

            # If we have an array holding the taxi out track, then we use
            # this, otherwise we hold at the startpoint.
            if lat_out is not None and lat_out.size:
                lat_adj[:toff_slice.start] = lat_out
            else:
                lat_adj[:toff_slice.start] = lat_adj[toff_slice.start]

            if lon_out is not None and lon_out.size:
                lon_adj[:toff_slice.start] = lon_out
            else:
                lon_adj[:toff_slice.start] = lon_adj[toff_slice.start]

        else:
            print 'Cannot smooth taxi out'

        #-----------------------------------------------------------------------
        # Use ILS track for approach and landings in all localizer approches
        #-----------------------------------------------------------------------

        for approach in approaches:

            this_app_slice = approach.slice
            
            tdwn_index = None
            for tdwn in tdwns:
                if not is_index_within_slice(tdwn.index, this_app_slice):
                    continue
                else:
                    tdwn_index = tdwn.index

            runway = approach.runway
            if not runway:
                continue

            # We only refine the approach track if the aircraft lands off the localizer based approach.
            if approach.loc_est and is_index_within_slice(tdwn_index, approach.loc_est):
                this_loc_slice = approach.loc_est

                # Adjust the ils data to be degrees from the reference point.
                scale = localizer_scale(runway)
                bearings = (ils_loc.array[this_loc_slice] * scale + \
                            runway_heading(runway)+180.0)%360.0

                if precise:

                    # Tweek the localizer position to be on the start:end centreline
                    localizer_on_cl = ils_localizer_align(runway)

                    # Find distances from the localizer
                    _, distances = bearings_and_distances(lat.array[this_loc_slice],
                                                          lon.array[this_loc_slice],
                                                          localizer_on_cl)


                    # At last, the conversion of ILS localizer data to latitude and longitude
                    lat_adj[this_loc_slice], lon_adj[this_loc_slice] = \
                        latitudes_and_longitudes(bearings, distances, localizer_on_cl)

                else: # Imprecise navigation but with an ILS tuned.

                    # Adjust distance units
                    distances = app_range.array[this_loc_slice]

                    ## This test was introduced as a  precaution against poor 
                    ## quality data, but in fact for landings where only airspeed 
                    ## data is available, none of the data below 60kt will be valid, 
                    ## hence this test was removed.
                    ##if np.ma.count(distances)/float(len(distances)) < 0.8:
                        ##continue # Insufficient range data to make this worth computing.

                    # Tweek the localizer position to be on the start:end centreline
                    localizer_on_cl = ils_localizer_align(runway)

                    # At last, the conversion of ILS localizer data to latitude and longitude
                    lat_adj[this_loc_slice], lon_adj[this_loc_slice] = \
                        latitudes_and_longitudes(bearings, distances,
                                                 localizer_on_cl)

                # Alignment of the ILS Localizer Range causes corrupt first
                # samples.
                lat_adj[this_loc_slice.start] = np.ma.masked
                lon_adj[this_loc_slice.start] = np.ma.masked

                ils_join_offset = None
                if approach.type == 'LANDING':
                    # Remember where we lost the ILS, in preparation for the taxi in.
                    ils_join, _ = last_valid_sample(lat_adj[this_loc_slice])
                    if ils_join:
                        ils_join_offset = this_loc_slice.start + ils_join

            else:
                # No localizer in this approach

                if precise:
                    # Without an ILS we can do no better than copy the prepared arrray data forwards.
                    lat_adj[this_app_slice] = lat.array[this_app_slice]
                    lon_adj[this_app_slice] = lon.array[this_app_slice]
                else:
                    '''
                    We need to fix the bottom end of the descent without an
                    ILS to fix. The best we can do is put the touchdown point
                    in the right place. (An earlier version put the track
                    onto the runway centreline which looked convincing, but
                    went disasterously wrong for curving visual approaches
                    into airfields like Nice).
                    '''
                    # Q: Currently we rely on a Touchdown KTI existing to smooth
                    #    a track without the ILS Localiser being established or
                    #    precise positioning. This is to ensure that the
                    #    aircraft is on the runway and therefore we can use
                    #    database coordinates for the runway to smooth the
                    #    track. This does not provide a solution for aircraft
                    #    which do not momentarily land on the runway. Could we
                    #    assume that the aircraft will match the runway
                    #    coordinates if it drops below a certain altitude as
                    #    this will be more accurate than low precision
                    #    positioning equipment.
                    
                    if not tdwn_index:
                        continue

                    # Adjust distance units
                    distance = np.ma.array([value_at_index(app_range.array, tdwn_index)])
                    bearing = np.ma.array([(runway_heading(runway)+180)%360.0])
                    # Reference point for visual approaches is the runway end.
                    ref_point = runway['end']

                    # Work out the touchdown point
                    lat_tdwn, lon_tdwn = latitudes_and_longitudes \
                        (bearing, distance, ref_point)

                    lat_err = value_at_index(lat.array, tdwn_index) - lat_tdwn
                    lon_err = value_at_index(lon.array, tdwn_index) - lon_tdwn
                    lat_adj[this_app_slice] = lat.array[this_app_slice] - lat_err
                    lon_adj[this_app_slice] = lon.array[this_app_slice] - lon_err

            # The computation of a ground track is not ILS dependent and does
            # not depend upon knowing the runway details.
            if approach.type == 'LANDING':
                # This function returns the lowest non-None offset.
                join_idx = min(filter(bool, [ils_join_offset,
                                             approach.turnoff]))

                if join_idx and (len(lat_adj) > join_idx): # We have some room to extend over.

                    if precise:
                        # Set up the point of handover
                        lat.array[join_idx] = lat_adj[join_idx]
                        lon.array[join_idx] = lon_adj[join_idx]
                        try:
                            lat_in, lon_in = self.taxi_in_track_pp(
                                lat.array[join_idx:end],
                                lon.array[join_idx:end],
                                speed[join_idx:end],
                                hdg.array[join_idx:end],
                                freq)
                        except ValueError:
                            self.exception("'%s'. Using non smoothed coordinates for Taxi In",
                                           self.__class__.__name__)
                            lat_in = lat.array[join_idx:end]
                            lon_in = lon.array[join_idx:end]
                    else:
                        if join_idx and (len(lat_adj) > join_idx):
                            scan_back = slice(join_idx, this_app_slice.start, -1)
                            lat_join = first_valid_sample(lat_adj[scan_back])
                            lon_join = first_valid_sample(lon_adj[scan_back])
                            if lat_join.index and lon_join.index:
                                join_idx -= max(lat_join.index, lon_join.index) # step back to make sure the join location is not masked.
                                lat_in, lon_in = self.taxi_in_track(
                                    lat_adj[join_idx:end],
                                    lon_adj[join_idx:end],
                                    speed[join_idx:end],
                                    hdg.array[join_idx:end],
                                    freq,
                                )
                            else:
                                lat_in = lon_in = None

                    # If we have an array of taxi in track values, we use
                    # this, otherwise we hold at the end of the landing.
                    if lat_in is not None and np.ma.count(lat_in):
                        lat_adj[join_idx:end] = lat_in
                    else:
                        lat_adj[join_idx:end] = lat_adj[join_idx]
                        
                    if lon_in is not None and np.ma.count(lon_in):
                        lon_adj[join_idx:end] = lon_in
                    else:
                        lon_adj[join_idx:end] = lon_adj[join_idx]

        return lat_adj, lon_adj


class LatitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    """
    From a prepared Latitude parameter, which may have been created by
    straightening out a recorded latitude data set, or from an estimate using
    heading and true airspeed, we now match the data to the available runway
    data. (Airspeed is included as an alternative to groundspeed so that the
    algorithm has wider applicability).

    Where possible we use ILS data to make the landing data as accurate as
    possible, and we create ground track data with groundspeed and heading if
    available.

    Once these sections have been created, the parts are 'stitched' together
    to make a complete latitude trace.

    The first parameter in the derive method is heading_continuous, which is
    always available and which should always have a sample rate of 1Hz. This
    ensures that the resulting computations yield a smoothed track with 1Hz
    spacing, even if the recorded latitude and longitude have only 0.25Hz
    sample rate.
    """

    # List the minimum acceptable parameters here
    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Latitude Prepared',
            'Longitude Prepared',
            'Approach Range',
            'Airspeed True',
            'Precise Positioning',
            'Takeoff',
            'FDR Takeoff Runway',
            'Touchdown',
            'Approach Information',
            'Mobile'), available) \
               and any_of(('Heading True Continuous',
                           'Heading Continuous'), available)

    units = 'deg'

    def derive(self, lat=P('Latitude Prepared'),
               lon=P('Longitude Prepared'),
               hdg_mag=P('Heading Continuous'),
               ils_loc=P('ILS Localizer'),
               app_range=P('Approach Range'),
               hdg_true=P('Heading True Continuous'),
               gspd=P('Groundspeed'),
               tas=P('Airspeed True'),
               precise=A('Precise Positioning'),
               toff=S('Takeoff'),
               toff_rwy = A('FDR Takeoff Runway'),
               tdwns = S('Touchdown'),
               approaches = App('Approach Information'),
               mobile=S('Mobile'),
               ):
        precision = bool(getattr(precise, 'value', False))

        if hdg_true:
            hdg = hdg_true
        else:
            hdg = hdg_mag

        lat_adj, lon_adj = self._adjust_track(
            lon, lat, ils_loc, app_range, hdg, gspd, tas, toff, toff_rwy, tdwns,
            approaches, mobile, precision)
        self.array = track_linking(lat.array, lat_adj)


class LongitudeSmoothed(DerivedParameterNode, CoordinatesSmoothed):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'
    ##align_frequency = 1.0
    ##align_offset = 0.0

    @classmethod
    def can_operate(cls, available):
        return all_of((
            'Latitude Prepared',
            'Longitude Prepared',
            'Approach Range',
            'Airspeed True',
            'Precise Positioning',
            'Takeoff',
            'FDR Takeoff Runway',
            'Touchdown',
            'Approach Information',
            'Mobile'), available) \
               and any_of(('Heading True Continuous',
                           'Heading Continuous'), available)

    def derive(self, lat = P('Latitude Prepared'),
               lon = P('Longitude Prepared'),
               hdg_mag=P('Heading Continuous'),
               ils_loc = P('ILS Localizer'),
               app_range = P('Approach Range'),
               hdg_true = P('Heading True Continuous'),
               gspd = P('Groundspeed'),
               tas = P('Airspeed True'),
               precise =A('Precise Positioning'),
               toff = S('Takeoff'),
               toff_rwy = A('FDR Takeoff Runway'),
               tdwns = S('Touchdown'),
               approaches = App('Approach Information'),
               mobile=S('Mobile'),
               ):
        precision = bool(getattr(precise, 'value', False))

        if hdg_true:
            hdg = hdg_true
        else:
            hdg = hdg_mag

        lat_adj, lon_adj = self._adjust_track(lon, lat, ils_loc, app_range, hdg,
                                            gspd, tas, toff, toff_rwy,
                                            tdwns, approaches, mobile, precision)
        self.array = track_linking(lon.array, lon_adj)
        

class Mach(DerivedParameterNode):
    '''
    Mach derived from air data parameters for aircraft where no suitable Mach
    data is recorded.
    '''

    units = 'Mach'

    def derive(self, cas = P('Airspeed'), alt = P('Altitude STD')):
        dp = cas2dp(cas.array)
        p = alt2press(alt.array)
        self.array = dp_over_p2mach(dp/p)


class MagneticVariation(DerivedParameterNode):
    """
    This computes magnetic declination values from latitude, longitude,
    altitude and date. Uses Latitude/Longitude or
    Latitude (Coarse)/Longitude (Coarse) parameters instead of Prepared or
    Smoothed to avoid cyclical dependencies.
    """

    units = 'deg'
    
    align_frequency = 1/4.0
    align_offset = 0.0

    @classmethod
    def can_operate(cls, available):
        lat = any_of(('Latitude', 'Latitude (Coarse)'), available)
        lon = any_of(('Longitude', 'Longitude (Coarse)'), available)
        return lat and lon and all_of(('Altitude AAL', 'Start Datetime'),
                                      available)

    def derive(self, lat=P('Latitude'), lat_coarse=P('Latitude (Coarse)'),
               lon=P('Longitude'), lon_coarse=P('Longitude (Coarse)'),
               alt_aal=P('Altitude AAL'), start_datetime=A('Start Datetime')):
        
        lat = lat or lat_coarse
        lon = lon or lon_coarse
        mag_var_frequency = 64 * self.frequency
        mag_vars = []
        if start_datetime.value:
            start_date = start_datetime.value.date()
        else:
            import datetime
            start_date = datetime.date.today()
            # logger.warn('Start date time set to today')


        # TODO: Optimize.
        for lat_val, lon_val, alt_aal_val in zip(lat.array[::mag_var_frequency],
                                                 lon.array[::mag_var_frequency],
                                                 alt_aal.array[::mag_var_frequency]):
            if np.ma.masked in (lat_val, lon_val, alt_aal_val):
                mag_vars.append(np.ma.masked)
            else:
                mag_vars.append(geomag.declination(lat_val, lon_val,
                                                   alt_aal_val,
                                                   time=start_date))
        
        # Repair mask to avoid interpolating between masked values.
        mag_vars = repair_mask(np.ma.array(mag_vars), extrapolate=True)
        interpolator = interp1d(
            np.arange(0, len(lat.array), mag_var_frequency), mag_vars)
        interpolation_length = (len(mag_vars) - 1) * mag_var_frequency
        array = np_ma_masked_zeros_like(lat.array)
        array[:interpolation_length] = \
            interpolator(np.arange(interpolation_length))
        
        # Exclude masked values.
        mask = lat.array.mask | lon.array.mask | alt_aal.array.mask
        array = np.ma.masked_where(mask, array)
        self.array = repair_mask(array, extrapolate=True,
                                 repair_duration=None)


class MagneticVariationFromRunway(DerivedParameterNode):
    """
    This computes local magnetic variation values on the runways and
    interpolates between one airport and the next. The values at each airport
    are kept constant.
    
    Runways identified by approaches are not included as the aircraft may
    have drift and therefore cannot establish the heading of the runway as it
    does not land on it.

    The main idea here is that we can easily identify the ends of the runway
    and the heading of the aircraft on the runway. This allows a Heading True
    to be derived from the aircraft's perceived magnetic variation. This is
    important as some aircraft's recorded Heading (magnetic) can be based
    upon magnetic variation from out of date databases. Also, by using the
    aircraft compass values to work out the variation, we inherently
    accommodate compass drift for that day.
    
    TODO: Instead of linear interpolation, perhaps base it on distance flown.
    """
    units = 'deg'
    align_frequency = 1/4.0
    align_offset = 0.0

    def derive(self, duration=A('HDF Duration'),
               head_toff = KPV('Heading During Takeoff'),
               head_land = KPV('Heading During Landing'),
               toff_rwy = A('FDR Takeoff Runway'),
               land_rwy = A('FDR Landing Runway')):
        array_len = duration.value * self.frequency
        dev = np.ma.zeros(array_len)
        dev.mask = True
        
        # takeoff
        tof_hdg_mag_kpv = head_toff.get_first()
        if tof_hdg_mag_kpv and toff_rwy:
            takeoff_hdg_mag = tof_hdg_mag_kpv.value
            try:
                takeoff_hdg_true = runway_heading(toff_rwy.value)
            except ValueError:
                # runway does not have coordinates to calculate true heading
                pass
            else:
                dev[tof_hdg_mag_kpv.index] = takeoff_hdg_true - takeoff_hdg_mag
        
        # landing
        ldg_hdg_mag_kpv = head_land.get_last()
        if ldg_hdg_mag_kpv and land_rwy:
            landing_hdg_mag = ldg_hdg_mag_kpv.value
            try:
                landing_hdg_true = runway_heading(land_rwy.value)
            except ValueError:
                # runway does not have coordinates to calculate true heading
                pass
            else:
                dev[ldg_hdg_mag_kpv.index] = landing_hdg_true - landing_hdg_mag

        # linearly interpolate between values and extrapolate to ends of the
        # array, even if only the takeoff variation is calculated as the
        # landing variation is more likely to be the same as takeoff than 0
        # degrees (and vice versa).
        self.array = interpolate(dev, extrapolate=True)


class VerticalSpeedInertial(DerivedParameterNode):
    '''
    See 'Vertical Speed' for pressure altitude based derived parameter.
    
    If the aircraft records an inertial vertical speed, rename this "Vertical
    Speed Inertial - Recorded" to avoid conflict

    This routine derives the vertical speed from the vertical acceleration, the
    Pressure altitude and the Radio altitude.

    Long term errors in the accelerometers are removed by washing out the
    acceleration term with a longer time constant filter before use. The
    consequence of this is that long period movements with continued
    acceleration will be underscaled slightly. As an example the test case
    with a 1ft/sec^2 acceleration results in an increasing vertical speed of
    55 fpm/sec, not 60 as would be theoretically predicted.

    Complementary first order filters are used to combine the acceleration
    data and the height data. A high pass filter on the altitude data and a
    low pass filter on the acceleration data combine to form a consolidated
    signal.
    
    See also http://www.flightdatacommunity.com/inertial-smoothing.
    '''

    units = 'fpm'

    def derive(self,
               az = P('Acceleration Vertical'),
               alt_std = P('Altitude STD Smoothed'),
               alt_rad = P('Altitude Radio'),
               fast = S('Fast')):

        def inertial_vertical_speed(alt_std_repair, frequency, alt_rad_repair,
                                    az_repair):
            # Uses the complementary smoothing approach

            # This is the accelerometer washout term, with considerable gain.
            # The initialisation "initial_value=az_repair[0]" is very
            # important, as without this the function produces huge spikes at
            # each start of a data period.
            az_washout = first_order_washout (az_repair,
                                              AZ_WASHOUT_TC, frequency,
                                              gain=GRAVITY_IMPERIAL,
                                              initial_value=np.ma.mean(az_repair[0:40]))
            inertial_roc = first_order_lag (az_washout,
                                            VERTICAL_SPEED_LAG_TC,
                                            frequency,
                                            gain=VERTICAL_SPEED_LAG_TC)

            # We only differentiate the pressure altitude data.
            roc_alt_std = first_order_washout(alt_std_repair,
                                              VERTICAL_SPEED_LAG_TC, frequency,
                                              gain=1/VERTICAL_SPEED_LAG_TC)

            roc = (roc_alt_std + inertial_roc)
            hz = az.frequency
            
            # Between 100ft and the ground, replace the computed data with a
            # purely inertial computation to avoid ground effect.
            climbs = slices_from_to(alt_rad_repair, 0, 100)[1]
            for climb in climbs:
                # From 5 seconds before lift to 100ft
                lift_m5s = max(0, climb.start - 5*hz)
                up = slice(lift_m5s if lift_m5s >= 0 else 0, climb.stop)
                up_slope = integrate(az_washout[up], hz)
                blend_end_error = roc[climb.stop-1] - up_slope[-1]
                blend_slope = np.linspace(0.0, blend_end_error, climb.stop-climb.start)
                roc[:lift_m5s] = 0.0
                roc[lift_m5s:climb.start] = up_slope[:climb.start-lift_m5s]
                roc[climb] = up_slope[climb.start-lift_m5s:] + blend_slope
                '''
                # Debug plot only.
                import matplotlib.pyplot as plt
                plt.plot(az_washout[up],'k')
                plt.plot(up_slope, 'g')
                plt.plot(roc[up],'r')
                plt.plot(alt_rad_repair[up], 'c')
                plt.show()
                plt.clf()
                plt.close()
                '''
                
            descents = slices_from_to(alt_rad_repair, 100, 0)[1]
            for descent in descents:
                down = slice(descent.start, descent.stop+5*hz)
                down_slope = integrate(az_washout[down], 
                                       hz,)
                blend = roc[down.start] - down_slope[0]
                blend_slope = np.linspace(blend, -down_slope[-1], len(down_slope))
                roc[down] = down_slope + blend_slope
                roc[descent.stop+5*hz:] = 0.0
                '''
                # Debug plot only.
                import matplotlib.pyplot as plt
                plt.plot(az_washout[down],'k')
                plt.plot(down_slope,'g')
                plt.plot(roc[down],'r')
                plt.plot(blend_slope,'b')
                plt.plot(down_slope + blend_slope,'m')
                plt.plot(alt_rad_repair[down], 'c')
                plt.show()
                plt.close()
                '''

            return roc * 60.0

        # Make space for the answers
        self.array = np_ma_masked_zeros_like(alt_std.array)
        hz = az.frequency
        
        for speedy in fast:
            # Fix minor dropouts
            az_repair = repair_mask(az.array[speedy.slice], 
                                    frequency=hz)
            alt_rad_repair = repair_mask(alt_rad.array[speedy.slice], 
                                         frequency=hz,
                                         repair_duration=None)
            alt_std_repair = repair_mask(alt_std.array[speedy.slice], 
                                         frequency=hz)
    
            # np.ma.getmaskarray ensures we have complete mask arrays even if
            # none of the samples are masked (normally returns a single
            # "False" value. We ignore the rad alt mask because we are only
            # going to use the radio altimeter values below 100ft, and short
            # transients will have been repaired. By repairing with the
            # repair_duration=None option, we ignore the masked saturated
            # values at high altitude.
    
            az_masked = np.ma.array(data = az_repair.data,
                                    mask = np.ma.logical_or(
                                        np.ma.getmaskarray(az_repair),
                                        np.ma.getmaskarray(alt_std_repair)))
    
            # We are going to compute the answers only for ranges where all
            # the required parameters are available.
            clumps = np.ma.clump_unmasked(az_masked)
            for clump in clumps:
                self.array[shift_slice(clump,speedy.slice.start)] = inertial_vertical_speed(
                    alt_std_repair[clump], az.frequency,
                    alt_rad_repair[clump], az_repair[clump])


class VerticalSpeed(DerivedParameterNode):
    '''
    The period for averaging altitude data is a trade-off between transient
    response and noise rejection.

    Some older aircraft have poor resolution, and the 4 second timebase
    leaves a noisy signal. We have inspected Hercules data, where the
    resolution is of the order of 9 ft/bit, and data from the BAe 146 where
    the resolution is 15ft and 737-6 frames with 32ft resolution. In these
    cases the wider timebase with greater smoothing is necessary, albeit at
    the expense of transient response.

    For most aircraft however, a period of 4 seconds is used. This has been
    found to give good results, and is also the value used to compute the
    recorded Vertical Speed parameter on Airbus A320 series aircraft
    (although in that case the data is delayed, and the aircraft cannot know
    the future altitudes!).
    '''

    units = 'fpm'

    @classmethod
    def can_operate(cls, available):
        return 'Altitude STD Smoothed' in available

    def derive(self, alt_std=P('Altitude STD Smoothed'), frame=A('Frame')):
        frame_name = frame.value if frame else ''

        if frame_name in ['146'] or \
           frame_name.startswith('747-200') or \
           frame_name.startswith('737-6'):
            self.array = rate_of_change(alt_std, 11.0) * 60.0
        elif frame_name in ['L382-Hercules']:
            self.array = rate_of_change(alt_std, 15.0, method='regression') * 60.0
        else:
            self.array = rate_of_change(alt_std, 4.0) * 60.0


class VerticalSpeedForFlightPhases(DerivedParameterNode):
    """
    A simple and robust vertical speed parameter suitable for identifying
    flight phases. DO NOT use this for event detection.
    """

    units = 'fpm'

    def derive(self, alt_std = P('Altitude STD Smoothed')):
        # This uses a scaled hysteresis parameter. See settings for more detail.
        threshold = HYSTERESIS_FPROC * max(1, rms_noise(alt_std.array))
        # The max(1, prevents =0 case when testing with artificial data.
        self.array = hysteresis(rate_of_change(alt_std, 6) * 60, threshold)


class Relief(DerivedParameterNode):
    """
    Also known as Terrain, this is zero at the airfields. There is a small
    cliff in mid-flight where the Altitude AAL changes from one reference to
    another, however this normally arises where Altitude Radio is out of its
    operational range, so will be masked from view.
    """

    units = 'ft'

    def derive(self, alt_aal = P('Altitude AAL'),
               alt_rad = P('Altitude Radio')):
        self.array = alt_aal.array - alt_rad.array


class CoordinatesStraighten(object):
    '''
    Superclass for LatitudePrepared and LongitudePrepared.
    '''
    def _smooth_coordinates(self, coord1, coord2):
        """
        Acceleration along track only used to determine the sample rate and
        alignment of the resulting smoothed track parameter.

        :param coord1: Either 'Latitude' or 'Longitude' parameter.
        :type coord1: DerivedParameterNode
        :param coord2: Either 'Latitude' or 'Longitude' parameter.
        :type coord2: DerivedParameterNode
        :returns: coord1 smoothed.
        :rtype: np.ma.masked_array
        """
        coord1_s = coord1.array
        coord2_s = coord2.array

        # Join the masks, so that we only consider positional data when both are valid:
        coord1_s.mask = np.ma.logical_or(np.ma.getmaskarray(coord1.array),
                                         np.ma.getmaskarray(coord2.array))
        coord2_s.mask = np.ma.getmaskarray(coord1_s)
        # Preload the output with masked values to keep dimension correct
        array = np_ma_masked_zeros_like(coord1_s)

        # Now we just smooth the valid sections.
        tracks = np.ma.clump_unmasked(coord1_s)
        for track in tracks:
            # Reject any data with invariant positions, i.e. sitting on stand.
            if np.ma.ptp(coord1_s[track])>0.0 and np.ma.ptp(coord2_s[track])>0.0:
                coord1_s_track, coord2_s_track, cost = \
                    smooth_track(coord1_s[track], coord2_s[track], coord1.frequency)
                array[track] = coord1_s_track
        return array


class LongitudePrepared(DerivedParameterNode, CoordinatesStraighten):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return all_of(('Latitude', 'Longitude'), available) or\
               (all_of(('Airspeed True',
                        'Latitude At Liftoff',
                        'Longitude At Liftoff',
                        'Latitude At Touchdown',
                        'Longitude At Touchdown'), available) and\
                any_of(('Heading', 'Heading True'), available))

    # Note hdg is alignment master to force 1Hz operation when latitude &
    # longitude are only recorded at 0.25Hz.
    def derive(self,
               lat=P('Latitude'), lon=P('Longitude'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               lat_lift=KPV('Latitude At Liftoff'),
               lon_lift=KPV('Longitude At Liftoff'),
               lat_land=KPV('Latitude At Touchdown'),
               lon_land=KPV('Longitude At Touchdown')):

        if lat and lon:
            """
            This removes the jumps in longitude arising from the poor resolution of
            the recorded signal.
            """
            self.array = self._smooth_coordinates(lon, lat)
        else:
            if hdg_true:
                hdg = hdg_true
            else:
                hdg = hdg_mag
            _, lon_array = air_track(
                lat_lift.get_first().value, lon_lift.get_first().value,
                lat_land.get_last().value, lon_land.get_last().value,
                tas.array, hdg.array, tas.frequency)
            self.array = lon_array

class LatitudePrepared(DerivedParameterNode, CoordinatesStraighten):
    """
    See Latitude Smoothed for notes.
    """

    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return all_of(('Latitude', 'Longitude'), available) or \
               (all_of(('Airspeed True',
                        'Latitude At Liftoff',
                        'Longitude At Liftoff',
                        'Latitude At Touchdown',
                        'Longitude At Touchdown'), available) and \
                any_of(('Heading', 'Heading True'), available))

    # Note hdg is alignment master to force 1Hz operation when latitude &
    # longitude are only recorded at 0.25Hz.
    def derive(self,
               lat=P('Latitude'), lon=P('Longitude'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               lat_lift=KPV('Latitude At Liftoff'),
               lon_lift=KPV('Longitude At Liftoff'),
               lat_land=KPV('Latitude At Touchdown'),
               lon_land=KPV('Longitude At Touchdown')):

        if lat and lon:
            self.array = self._smooth_coordinates(lat, lon)
        else:
            if hdg_true:
                hdg = hdg_true
            else:
                hdg = hdg_mag
            lat_array, _ = air_track(
                lat_lift.get_first().value, lon_lift.get_first().value,
                lat_land.get_last().value, lon_land.get_last().value,
                tas.array, hdg.array, tas.frequency)
            self.array = lat_array


class RateOfTurn(DerivedParameterNode):
    """
    Simple rate of change of heading.
    """

    units = 'deg/sec'

    def derive(self, head=P('Heading Continuous')):
        # add a little hysteresis to the rate of change to smooth out minor
        # changes
        roc = rate_of_change(head, 4)
        self.array = hysteresis(roc, 0.1)
        # trouble is that we're loosing the nice 0 values, so force include!
        self.array[(self.array <= 0.05) & (self.array >= -0.05)] = 0


class Pitch(DerivedParameterNode):
    """
    Combination of pitch signals from two sources where required.
    """
    units = 'deg'
    align = False
    def derive(self, p1=P('Pitch (1)'), p2=P('Pitch (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(p1, p2)


class PitchRate(DerivedParameterNode):
    """
    Computes rate of change of pitch attitude over a two second period.

    Comment: A two second period is used to remove excessive short period
    transients which the pilot could not realistically be asked to control.
    It also means that low sample rate data (some aircraft have 
    pitch sampled at 1Hz) will still give comparable results. The drawback is
    that very brief transients, for example due to rough handling or
    turbulence, will not be detected.
    
    The rate_of_change algorithm was extended to allow regression
    calculation. This provides a best fit slope over the two second period,
    and so reduces the sensitivity to single samples, but tends to increase
    the peak values. As this also makes the resulting computation suffer more
    from masked values, and increases the computing load, it was decided not
    to implement this for pitch and roll rates.
    
    http://www.flightdatacommunity.com/calculating-pitch-rate/
    """

    units = 'deg/sec'

    def derive(self, pitch=P('Pitch'), frame=A('Frame')):
        frame_name = frame.value if frame else ''
        
        if frame_name in ['L382-Hercules']:
            self.array = rate_of_change(pitch, 8.0, method='regression')
        else:
            # See http://www.flightdatacommunity.com/blog/ for commentary on pitch rate techniques.
            self.array = rate_of_change(pitch, 2.0)


class Roll(DerivedParameterNode):
    """
    Combination of roll signals from two sources where required.
    """
    @classmethod
    def can_operate(cls, available):
        return 'Heading Continuous' in available
    
    units = 'deg'
    align = False
    
    def derive(self, r1=P('Roll (1)'), r2=P('Roll (2)'), 
               hdg=P('Heading Continuous'), frame=A('Frame')):
        frame_name = frame.value if frame else ''
        
        if r1 and r2:
            # Merge data from two sources.
            self.array, self.frequency, self.offset = \
                blend_two_parameters(r1, r2)
        
        elif frame_name in ['L382-Hercules', '1900D-SS542A']:
            # Added Beechcraft as had inoperable Roll
            # Many Hercules aircraft do not have roll recorded. This is a
            # simple substitute, derived from examination of the roll vs
            # heading rate of aircraft with a roll sensor.
            self.array = 6.0 * rate_of_change(hdg, 12.0, method='regression')
            self.frequency = hdg.frequency
            self.offset = hdg.offset

        else:
            raise DataFrameError(self.name, frame_name)


class RollRate(DerivedParameterNode):
    # TODO: Tests.

    '''
    The computational principles here are similar to Pitch Rate; see
    commentary for that parameter.
    '''
    
    units = 'deg/sec'

    def derive(self, roll=P('Roll')):
        self.array = rate_of_change(roll, 2.0)


class RudderPedal(DerivedParameterNode):
    '''
    See Elevator Left for description
    '''
    @classmethod
    def can_operate(cls, available):
        return any_of((
            'Rudder Pedal (L)',
            'Rudder Pedal (R)',
            'Rudder Pedal Potentiometer', 
            'Rudder Pedal Synchro',
            ), available)
    
    def derive(self, rudder_pedal_l=P('Rudder Pedal (L)'),
               rudder_pedal_r=P('Rudder Pedal (R)'),
               pot=P('Rudder Pedal Potentiometer'),
               synchro=P('Rudder Pedal Synchro')):
        
        if rudder_pedal_l or rudder_pedal_r:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(rudder_pedal_l, rudder_pedal_r)
        
        synchro_samples = 0
        
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.frequency = synchro.frequency
            self.offset = synchro.offset
            self.array = synchro.array
            
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples > synchro_samples:
                self.frequency = pot.frequency
                self.offset = pot.offset
                self.array = pot.array
        

class ThrottleLevers(DerivedParameterNode):
    """
    A synthetic throttle lever angle, based on the average of the two. Allows
    for simple identification of changes in power etc.
    """

    align = False
    units = 'deg'

    def derive(self,
               tla1=P('Eng (1) Throttle Lever'),
               tla2=P('Eng (2) Throttle Lever')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(tla1, tla2)


class ThrustAsymmetry(DerivedParameterNode):
    '''
    Thrust asymmetry based on N1.

    For EPR rated aircraft, this measure should still be applicable as we are
    not applying a manufacturer's limit to the value, rather this is being
    used to identify imbalance of thrust and as the thrust comes from engine
    speed, N1 is still applicable.

    Using a 5 second moving average to desensitise the parameter against
    transient differences as engines accelerate.

    If we have EPR rated engines, but no N1 recorded, a possible solution
    would be to treat EPR=2.0 as 100% and EPR=1.0 as 0% so the Thrust
    Asymmetry would be simply (EPRmax-EPRmin)*100.

    For propeller aircraft the product of prop speed and torgue should be
    used to provide a similar single asymmetry value.
    '''

    units = '%'

    def derive(self, max_n1=P('Eng (*) N1 Max'), min_n1=P('Eng (*) N1 Min')):
        window = 5 * self.frequency # 5 second window
        self.array = moving_average(max_n1.array - min_n1.array, window=window)


class TurbulenceRMSG(DerivedParameterNode):
    """
    Simple RMS g measurement of turbulence over a 5-second period.
    """

    name = 'Turbulence RMS g'
    units = 'RMS g'

    def derive(self, acc=P('Acceleration Vertical')):
        width=int(acc.frequency*5+1)
        mean = moving_average(acc.array, window=width)
        acc_sq = (acc.array)**2.0
        n__sum_sq = moving_average(acc_sq, window=width)
        # Rescaling required as moving average is over width samples, whereas
        # we have only width - 1 gaps; fences and fence posts again !
        core = (n__sum_sq - mean**2.0)*width/(width-1.0)
        self.array = np.ma.sqrt(core)

#------------------------------------------------------------------
# WIND RELATED PARAMETERS
#------------------------------------------------------------------
class WindDirectionContinuous(DerivedParameterNode):
    """
    Like the aircraft heading, this does not jump as it passes through North.
    """
    units = 'deg'
    def derive(self, wind_head=P('Wind Direction')):
        self.array = straighten_headings(wind_head.array)

class WindDirectionTrueContinuous(DerivedParameterNode):
    """
    Like the aircraft heading, this does not jump as it passes through North.
    """
    units = 'deg'
    def derive(self, wind_head=P('Wind Direction True')):
        self.array = straighten_headings(wind_head.array)

class Headwind(DerivedParameterNode):
    """
    This is the headwind, negative values are tailwind.
    """

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        if all_of(('Wind Speed',
                   'Wind Direction Continuous',
                   'Heading True Continuous'), available):
            return True

    def derive(self, windspeed=P('Wind Speed'),
               wind_dir=P('Wind Direction Continuous'),
               head=P('Heading True Continuous'),
               toffs=S('Takeoff'),
               alt_aal=P('Altitude AAL'),
               gspd=P('Groundspeed'),
               aspd=P('Airspeed True')):

        rad_scale = radians(1.0)
        headwind = windspeed.array * np.ma.cos((wind_dir.array-head.array)*rad_scale)

        # If we have airspeed and groundspeed, overwrite the values for the
        # first hundred feet after takeoff. Note this is done in a
        # deliberately crude manner so that the different computations may be
        # identified easily by the analyst.
        if gspd and aspd and alt_aal and toffs:
            # We merge takeoff slices with altitude slices to extend the takeoff phase to 100ft.
            for climb in slices_or(alt_aal.slices_from_to(0, 100),
                                   [s.slice for s in toffs]):
                headwind[climb] = aspd.array[climb] - gspd.array[climb]

        self.array = headwind


class Tailwind(DerivedParameterNode):
    """
    This is the tailwind component.
    """

    units = 'kts'

    def derive(self, hwd=P('Headwind')):
        self.array = -hwd.array


class SAT(DerivedParameterNode):
    """
    Computes Static Air Temperature (temperature of the outside air) from the
    Total Air Temperature, allowing for compressibility effects, or if this
    is not available, the standard atmosphere and lapse rate.
    
    Q: Support transforming SAT from OAT (as they are equal).
    
    TODO: Review naming convention - rename to "Static Air Temperature"?
    """
    @classmethod
    def can_operate(cls, available):
        return True # As Altitude STD must always be available

    name = 'SAT'
    units = 'C'

    def derive(self, tat=P('TAT'), mach=P('Mach'), alt=P('Altitude STD')):
        if tat and mach:
            self.array = machtat2sat(mach.array, tat.array)
        else:
            self.array = alt2sat(alt.array)
    
    
class TAT(DerivedParameterNode):
    """
    Blends data from two air temperature sources.
    
    TODO: Support generation from SAT, Mach and Altitude STD    
    TODO: Review naming convention - rename to "Total Air Temperature"?
    """
    name = "TAT"
    units = 'C'
    align = False

    def derive(self,
               source_1 = P('TAT (1)'),
               source_2 = P('TAT (2)')):

        # Alternate samples (1)&(2) are blended.
        self.array, self.frequency, self.offset = \
            blend_two_parameters(source_1, source_2)


class V2(DerivedParameterNode):
    '''
    Derives a value for the V2 velocity speed.

    If no recorded value is available, a value provided in an achieved flight
    record will be used. Alternatively an attempt will be made to determine the
    value from other available parameters.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available):
        afr = all_of(('Airspeed', 'AFR V2'), available)
        airbus = all_of(('Airspeed', 'Auto Speed Control', 'Selected Speed'), available)
        return afr or airbus

    def derive(self,
               air_spd=P('Airspeed'),
               afr_v2=A('AFR V2'),
               spd_ctl=P('Auto Speed Control'),
               spd_sel=P('Selected Speed')):

        if afr_v2:
            # Use value supplied in achieved flight record:
            self.array = np_ma_ones_like(air_spd.array) * afr_v2.value
        elif spd_sel:
            # Determine value for some Airbus aircraft:
            self.array = np.ma.where(spd_ctl.array == 1, spd_sel.array, np.ma.masked)
        else:
            # Unable to determine - use zeroed masked array:
            self.array = np_ma_masked_zeros_like(air_spd.array)


class V2Lookup(DerivedParameterNode):
    '''
    Derives a value for the V2 velocity speed looked up from tables.

    The value will be looked up based on weight and flap (surface detents) at
    liftoff. Only the first liftoff will be used.

    Flap is used as the first dependency to avoid interpolation of flap detents
    when flap is recorded at a lower frequency than airspeed.
    '''

    units = 'kts'

    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family'),
                    engine=A('Engine Series'), engine_type=A('Engine Type')):

        try:
            cls._get_vspeed_class(series, family, engine,
                                  engine_type)().tables['v2']
        except KeyError:
            return False

        x = set(available)
        base = ['Airspeed']
        weight = base + ['Gross Weight At Liftoff']
        airbus = set(weight + ['Configuration']).issubset(x)
        boeing = set(weight + ['Flap']).issubset(x)
        propeller = set(base + ['Eng (*) Np Avg', 'Liftoff']).issubset(x)
        # FIXME: Replace the flaky logic for small propeller aircraft which do
        #        not record gross weight, cannot provide achieved flight
        #        records and will be using a fixed value for processing.
        return airbus or boeing  # or propeller

    @staticmethod
    def _get_vspeed_class(series, family, engine, engine_type):
        x = map(lambda x: x.value if x else None,
                (series, family, engine, engine_type))
        return get_vspeed_map(*x)

    def derive(self,
               flap=M('Flap'),
               conf=P('Configuration'),
               air_spd=P('Airspeed'),
               weight_liftoffs=KPV('Gross Weight At Liftoff'),
               series=A('Series'),
               family=A('Family'),
               engine=A('Engine Series'),
               engine_type=A('Engine Type'),
               v2=P('V2'),
               liftoffs=KTI('Liftoff'),
               eng_np=P('Eng (*) Np Avg')):

        # Initialize the result space.
        self.array = np_ma_masked_zeros_like(air_spd.array)

        try:
            vspeed_class = self._get_vspeed_class(series, family, engine,
                                                  engine_type)
        except KeyError as err:
            if v2:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            return

        # check Conf as is dependant on Flap
        setting_array = conf.array if conf else flap.array.raw
        vspeed_table = vspeed_class()

        if weight_liftoffs is not None:
            # Explicitly looking for no Gross Weight At Liftoff node, as
            # opposed to having a node with no KPVs
            weight_liftoff = weight_liftoffs.get_first()
            index, weight = weight_liftoff.index, weight_liftoff.value
        else:
            index, weight = liftoffs.get_first().index, None

        setting = setting_array[index]

        try:
            vspeed = vspeed_table.v2(setting, weight)
        except (KeyError, ValueError) as err:
            if v2:
                self.info("Error in '%s': %s", self.name, err)
            else:
                self.warning("Error in '%s': %s", self.name, err)
            # Where the aircraft takes off with flap settings outside the
            # documented V2 range, we need the program to continue without
            # raising an exception, so that the incorrect flap at takeoff
            # can be detected.
            return

        if vspeed is not None:
            self.array[0:] = vspeed
        else:
            self.array[0:] = np.ma.masked


class WindAcrossLandingRunway(DerivedParameterNode):
    """
    This is the windspeed across the final landing runway, positive wind from left to right.
    """
    units = 'kts'
    
    @classmethod
    def can_operate(cls, available):
        return all_of(('Wind Speed', 'Wind Direction True Continuous', 'FDR Landing Runway'), available) \
               or \
               all_of(('Wind Speed', 'Wind Direction Continuous', 'Heading During Landing'), available)

    def derive(self, windspeed=P('Wind Speed'),
               wind_dir_true=P('Wind Direction True Continuous'),
               wind_dir_mag=P('Wind Direction Continuous'),
               land_rwy=A('FDR Landing Runway'),
               land_hdg=KPV('Heading During Landing')):

        if wind_dir_true and land_rwy:
            # proceed with "True" values
            wind_dir = wind_dir_true
            land_heading = runway_heading(land_rwy.value)
            self.array = np_ma_masked_zeros_like(wind_dir_true.array)
        elif wind_dir_mag and land_hdg:
            # proceed with "Magnetic" values
            wind_dir = wind_dir_mag
            land_heading = land_hdg.get_last().value
        else:
            # either no landing runway detected or no landing heading detected
            self.array = np_ma_masked_zeros_like(windspeed.array)
            self.warning('Cannot calculate without landing runway (%s) or landing heading (%s)',
                         bool(land_rwy), bool(land_hdg))
            return
        diff = (land_heading - wind_dir.array) * deg2rad
        self.array = windspeed.array * np.ma.sin(diff)


class Aileron(DerivedParameterNode):
    '''
    Aileron measures the roll control from the Left and Right Aileron
    signals. By taking the average of the two signals, any Flaperon movement
    is removed from the signal, leaving only the difference between the left
    and right which results in the roll control.
    
    Note: This requires that both Aileron signals have positive sign for
    positive (right) rolling moment. That is, port aileron down and starboard
    aileron up have positive sign.
    
    Note: This is NOT a multistate parameter - see Flaperon.
    '''
    align = True
    units = 'deg'

    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (L)', 'Aileron (R)'), available)

    def derive(self, al=P('Aileron (L)'), ar=P('Aileron (R)')):
        if al and ar:
            # Taking the average will ensure that positive roll to the right
            # on both signals is maintained as positive control, where as
            # any flaperon action (left positive, right negative) will
            # average out as no roll control.
            self.array = (al.array + ar.array) / 2
        else:
            ail = al or ar
            self.array = ail.array

            
class AileronLeft(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Aileron (L)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (L) Potentiometer', 
                       'Aileron (L) Synchro',
                       'Aileron (L) Inboard',
                       'Aileron (L) Outboard'), available)
    
    def derive(self, pot=P('Aileron (L) Potentiometer'),
               synchro=P('Aileron (L) Synchro'),
               ali=P('Aileron (L) Inboard'),
               alo=P('Aileron (L) Outboard')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        # If Inboard available, use this in preference
        if ali:
            self.array = ali.array
        elif alo:
            self.array = alo.array


class AileronRight(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Aileron (R)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Aileron (R) Potentiometer', 
                       'Aileron (R) Synchro',
                       'Aileron (R) Inboard',
                       'Aileron (R) Outboard'), available)
    
    def derive(self, pot=P('Aileron (R) Potentiometer'),
               synchro=P('Aileron (R) Synchro'),
               ari=P('Aileron (R) Inboard'),
               aro=P('Aileron (R) Outboard')):

        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array
        # If Inboard available, use this in preference
        if ari:
            self.array = ari.array
        elif aro:
            self.array = aro.array        


class AileronTrim(DerivedParameterNode): # RollTrim
    '''
    '''
    # TODO: TEST
    align = False
    name = 'Aileron Trim' # Roll Trim
    units = 'deg'

    def derive(self,
               atl=P('Aileron Trim (L)'),
               atr=P('Aileron Trim (R)')):
        self.array, self.frequency, self.offset = blend_two_parameters(atl, atr)


class Elevator(DerivedParameterNode):
    '''
    Blends alternate elevator samples. If either elevator signal is invalid,
    this reverts to just the working sensor.
    '''

    align = False
    units = 'deg'
    
    @classmethod
    def can_operate(cls,available):
        return any_of(('Elevator (L)', 'Elevator (R)'), available)

    def derive(self,
               el=P('Elevator (L)'),
               er=P('Elevator (R)')):

        if el and er:
            self.array, self.frequency, self.offset = blend_two_parameters(el, er)
        else:
            self.array = el.array if el else er.array
            self.frequency = el.frequency if el else er.frequency
            self.offset = el.offset if el else er.offset


class ElevatorLeft(DerivedParameterNode):
    '''
    Specific to a group of ATR aircraft which were progressively modified to
    replace potentiometers with synchros. The data validity tests will mark
    whole parameters invalid, or if both are valid, we want to pick the best
    option.
    '''
    name = 'Elevator (L)'
    
    @classmethod
    def can_operate(cls, available):
        return any_of(('Elevator (L) Potentiometer', 
                       'Elevator (L) Synchro'), available)
    
    def derive(self, pot=P('Elevator (L) Potentiometer'),
               synchro=P('Elevator (L) Synchro')):

        synchro_samples = 0
        
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
            
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array


class ElevatorRight(DerivedParameterNode):
    # See ElevatorLeft for explanation
    name = 'Elevator (R)'
    @classmethod
    def can_operate(cls, available):
        return any_of(('Elevator (R) Potentiometer', 
                       'Elevator (R) Synchro'), available)
    
    def derive(self, pot=P('Elevator (R) Potentiometer'),
               synchro=P('Elevator (R) Synchro')):
        synchro_samples = 0
        if synchro:
            synchro_samples = np.ma.count(synchro.array)
            self.array = synchro.array
        if pot:
            pot_samples = np.ma.count(pot.array)
            if pot_samples>synchro_samples:
                self.array = pot.array


###############################################################################
# Speedbrake


class Speedbrake(DerivedParameterNode):
    '''
    Spoiler angle in degrees, zero flush with the wing and positive up.

    Spoiler positions are recorded in different ways on different aircraft,
    hence the frame specific sections in this class.
    '''

    units = 'deg'
    align = False

    @classmethod
    def can_operate(cls, available, family=A('Family')):
        '''
        Note: The frame name cannot be accessed within this method to determine
              which parameters are required.
        '''
        family_name = family.value if family else None
        return family_name and (
            family_name == 'B737-Classic' and (
                'Spoiler (4)' in available or
                'Spoiler (9)' in available
            ) or
            # FIXME: this is currently used with frame A320_SFIM_ED45_CFM
            family_name in ['B737-NG', 'A320'] and (
                'Spoiler (2)' in available or
                'Spoiler (7)' in available
            ) or
            family_name == 'B787' and (
                'Spoiler (1)' in available or
                'Spoiler (14)' in available
            ) or
            family_name in ['G-V', 'Learjet'] and any_of((
                'Spoiler (L)',
                'Spoiler (R)'),
                available
            ) or
            family_name in ['CRJ 900'] and any_of((
                'Spoiler (L) Inboard',
                'Spoiler (L) Outboard',
                'Spoiler (R) Inboard',
                'Spoiler (R) Outboard'),
                available
            )
        )

    def merge_spoiler(self, spoiler_a, spoiler_b):
        '''
        We indicate the angle of the lower of the two raised spoilers, as
        this represents the drag element. Differential deployment is used to
        augments roll control, so the higher of the two spoilers is relating
        to roll control. Small values are ignored as these arise from control
        trim settings.
        '''
        offset = (spoiler_a.offset + spoiler_b.offset) / 2.0
        array = np.ma.minimum(spoiler_a.array, spoiler_b.array)
        # Force small angles to indicate zero:
        array = np.ma.where(array < 10.0, 0.0, array)
        return array, offset

    def derive(self,
               spoiler_1=P('Spoiler (1)'),
               spoiler_2=P('Spoiler (2)'),
               spoiler_4=P('Spoiler (4)'),
               spoiler_7=P('Spoiler (7)'),
               spoiler_9=P('Spoiler (9)'),
               spoiler_14=P('Spoiler (14)'),
               spoiler_L=P('Spoiler (L)'),
               spoiler_R=P('Spoiler (R)'),
               spoiler_LI=P('Spoiler (L) Inboard'),
               spoiler_LO=P('Spoiler (L) Outboard'),
               spoiler_RI=P('Spoiler (R) Inboard'),
               spoiler_RO=P('Spoiler (R) Outboard'),
               family=A('Family'),
               ):
        '''
        '''
        family_name = family.value

        if family_name == 'B737-Classic':
            self.array, self.offset = self.merge_spoiler(spoiler_4, spoiler_9)

        elif family_name in ['B737-NG', 'A320']:
            self.array, self.offset = self.merge_spoiler(spoiler_2, spoiler_7)

        elif family_name == '787':
            self.array, self.offset = self.merge_spoiler(spoiler_1, spoiler_14)

        elif family_name in ['G-V', 'Learjet']:
            self.array, self.offset = self.merge_spoiler(spoiler_L, spoiler_R)

        elif family_name in ['CRJ 900']:
            # First blend inboard and outboard, then merge
            spoiler_L = DerivedParameterNode(
                'Spoiler (L)', *blend_two_parameters(spoiler_LI, spoiler_LO))
            spoiler_R = DerivedParameterNode(
                'Spoiler (R)', *blend_two_parameters(spoiler_RI, spoiler_RO))
            self.array, self.offset = self.merge_spoiler(spoiler_L, spoiler_R)
            # FIXME: the parameter is not aligned
            self.frequency = spoiler_L.frequency

        else:
            raise DataFrameError(self.name, family_name)


class SpeedbrakeHandle(DerivedParameterNode):
    @classmethod
    def can_operate(cls, available):
        return any_of((
            'Speedbrake Handle (L)',
            'Speedbrake Handle (R)',
            'Speedbrake Handle (C)'
        ), available)

    def derive(self,
               sbh_l=M('Speedbrake Handle (L)'),
               sbh_r=M('Speedbrake Handle (R)'),
               sbh_c=M('Speedbrake Handle (C)')):

        available = [par for par in [sbh_l, sbh_r, sbh_c] if par]
        if len(available) > 1:
            self.array = blend_parameters(
                available, self.offset, self.frequency)
        elif len(available) == 1:
            self.array = available[0]


class ApproachRange(DerivedParameterNode):
    """
    This is the range to the touchdown point for both ILS and visual
    approaches including go-arounds. The reference point is the ILS Localizer
    antenna where the runway is so equipped, or the end of the runway where
    no ILS is available.

    The array is masked where no data has been computed, and provides
    measurements in metres from the reference point where the aircraft is on
    an approach.
    """

    units = 'm'

    @classmethod
    def can_operate(cls, available):
        return all_of((
                    'Airspeed True',
                    'Altitude AAL',
                    'Approach Information'), available) \
                       and any_of(('Heading True', 'Track True', 'Track'
                                   'Heading'), available)

    def derive(self, gspd=P('Groundspeed'),
               glide=P('ILS Glideslope'),
               trk_mag=P('Track'),
               trk_true=P('Track True'),
               hdg_mag=P('Heading'),
               hdg_true=P('Heading True'),
               tas=P('Airspeed True'),
               alt_aal=P('Altitude AAL'),
               approaches=App('Approach Information'),
               ):
        app_range = np_ma_masked_zeros_like(alt_aal.array)

        for approach in approaches:
            # We are going to reference the approach to a runway touchdown
            # point. Without that it's pretty meaningless, so give up now.
            runway = approach.runway
            if not runway:
                continue

            # Retrieve the approach slice
            this_app_slice = approach.slice

            # Let's use the best available information for this approach
            if trk_true and np.ma.count(trk_true.array[this_app_slice]):
                hdg = trk_true
                magnetic = False
            elif trk_mag and np.ma.count(trk_mag.array[this_app_slice]):
                hdg = trk_mag
                magnetic = True
            elif hdg_true and np.ma.count(hdg_true.array[this_app_slice]):
                hdg = hdg_true
                magnetic = False
            else:
                hdg = hdg_mag
                magnetic = True

            kwargs = {'runway': runway}
            
            if magnetic:
                try:
                    # If magnetic heading is being used get magnetic heading
                    # of runway
                    kwargs = {'heading': runway['magnetic_heading']}
                except KeyError:
                    # If magnetic heading is not know for runway fallback to
                    # true heading
                    pass

            # What is the heading with respect to the runway centreline for this approach?
            off_cl = runway_deviation(hdg.array[this_app_slice], **kwargs)

            # Use recorded groundspeed where available, otherwise
            # estimate range using true airspeed. This is because there
            # are aircraft which record ILS but not groundspeed data. In
            # either case the speed is referenced to the runway heading
            # in case of large deviations on the approach or runway.
            if gspd:
                speed = gspd.array[this_app_slice] * \
                    np.cos(np.radians(off_cl))
                freq = gspd.frequency
            
            if not gspd or not np.ma.count(speed):
                speed = tas.array[this_app_slice] * \
                    np.cos(np.radians(off_cl))
                freq = tas.frequency

            # Estimate range by integrating back from zero at the end of the
            # phase to high range values at the start of the phase.
            spd_repaired = repair_mask(speed, repair_duration=None,
                                       extrapolate=True)
            app_range[this_app_slice] = integrate(spd_repaired, freq,
                                                  scale=KTS_TO_MPS,
                                                  direction='reverse')
           
            _, app_slices = slices_between(alt_aal.array[this_app_slice],
                                           100, 500)
            # Computed locally, so app_slices do not need rescaling.
            if len(app_slices) != 1:
                self.info(
                    'Altitude AAL is not between 100-500 ft during an '
                    'approach slice. %s will not be calculated for this '
                    'section.', self.name)
                continue

            # reg_slice is the slice of data over which we will apply a
            # regression process to identify the touchdown point from the
            # height and distance arrays.
            reg_slice = shift_slice(app_slices[0], this_app_slice.start)
            
            gs_est = approach.gs_est
            if gs_est:
                # Compute best fit glidepath. The term (1-0.13 x glideslope
                # deviation) caters for the aircraft deviating from the
                # planned flightpath. 1 dot low is about 7% of a 3 degree
                # glidepath. Not precise, but adequate accuracy for the small
                # error we are correcting for here, and empyrically checked.
                corr, slope, offset = coreg(app_range[reg_slice],
                    alt_aal.array[reg_slice] * (1 - 0.13 * glide.array[reg_slice]))
                # This should correlate very well, and any drop in this is a
                # sign of problems elsewhere.
                if corr < 0.995:
                    self.warning('Low convergence in computing ILS '
                                 'glideslope offset.')
            else:
                # Just work off the height data assuming the pilot was aiming
                # to touchdown close to the glideslope antenna (for a visual
                # approach to an ILS-equipped runway) or at the touchdown
                # zone if no ILS glidepath is installed.
                corr, slope, offset = coreg(app_range[reg_slice],
                                            alt_aal.array[reg_slice])
                # This should still correlate pretty well, though not quite
                # as well as for a directed approach.
                if corr < 0.990:
                    self.warning('Low convergence in computing visual '
                                 'approach path offset.')

            ## This plot code allows the actual flightpath and regression line
            ## to be viewed in case of concern about the performance of the
            ## algorithm.
            ##import matplotlib.pyplot as plt
            ##x1=app_range[gs_est.start:this_app_slice.stop]
            ##y1=alt_aal.array[gs_est.start:this_app_slice.stop]
            ##x2=app_range[gs_est]
            ##y2=alt_aal.array[gs_est] * (1-0.13*glide.array[gs_est])
            ##xnew = np.linspace(np.min(x2),np.max(x2),num=2)
            ##ynew = (xnew - offset)/slope
            ##plt.plot(x1,y1,'-',x2,y2,'-',xnew,ynew,'-')
            ##plt.show()

            # Touchdown point nominally 1000ft from start of runway but
            # use glideslope antenna position if we know it.
            try:
                start_2_loc, gs_2_loc, end_2_loc, pgs_lat, pgs_lon = \
                    runway_distances(runway)
                extend = gs_2_loc
            except (KeyError, TypeError):
                extend = runway_length(runway) - 1000 / METRES_TO_FEET

            # Shift the values in this approach so that the range = 0 at
            # 0ft on the projected ILS or approach slope.
            app_range[this_app_slice] += extend - offset

        self.array = app_range

################################################################################


class VOR1Frequency(DerivedParameterNode):
    """
    Extraction of VOR tuned frequencies from receiver (1).
    """

    name = "VOR (1) Frequency"
    units = 'MHz'

    def derive(self, f=P('ILS-VOR (1) Frequency')):
        self.array = filter_vor_ils_frequencies(f.array, 'VOR')


class VOR2Frequency(DerivedParameterNode):
    """
    Extraction of VOR tuned frequencies from receiver (1).
    """

    name = "VOR (2) Frequency"
    units = 'MHz'

    def derive(self, f=P('ILS-VOR (2) Frequency')):
        self.array = filter_vor_ils_frequencies(f.array, 'VOR')

class WindSpeed(DerivedParameterNode):
    '''
    Required for Embraer 135-145 Data Frame
    '''

    align = False
    units = 'kts'

    def derive(self, wind_1=P('Wind Speed (1)'), wind_2=P('Wind Speed (2)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(wind_1, wind_2)


class WindDirectionTrue(DerivedParameterNode):
    """
    Compensates for magnetic variation, which will have been computed
    previously.
    """
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return 'Wind Direction' in available and \
               any_of(('Magnetic Variation From Runway', 'Magnetic Variation'),
                      available)
        
    def derive(self, wind=P('Wind Direction'),
               rwy_var=P('Magnetic Variation From Runway'),
               mag_var=P('Magnetic Variation')):
        if rwy_var and np.ma.count(rwy_var.array):
            # use this in preference
            var = rwy_var.array
        else:
            var = mag_var.array
        self.array = (wind.array + var) % 360.0


class WindDirection(DerivedParameterNode):
    '''
    Either combines two separate Wind Direction parameters.
    The Embraer 135-145 data frame includes two sources.
    '''
    
    align = False
    units = 'deg'
    
    @classmethod
    def can_operate(cls, available):
        return (('Wind Direction (1)' in available or
                 'Wind Direction (2)' in available) or
                ('Wind Direction True' in available and 
                 'Magnetic Variation' in available))
    
    def derive(self, wind_1=P('Wind Direction (1)'),
               wind_2=P('Wind Direction (2)'),
               wind_true=P('Wind Direction True'),
               mag_var=P('Magnetic Variation')):
        
        if wind_1 or wind_2:
            self.array, self.frequency, self.offset = \
                blend_two_parameters(wind_1, wind_2)
        else:
            self.frequency = wind_true.frequency
            self.offset = wind_true.offset
            self.array = (wind_true.array - align(mag_var, wind_true)) % 360.0


class WheelSpeedLeft(DerivedParameterNode):
    '''
    Merge the various recorded wheel speed signals from the left hand bogie.
    '''
    name = 'Wheel Speed (L)'
    align = False

    @classmethod
    def can_operate(cls, available):
        return 'Wheel Speed (L) (1)' in available
    
    def derive(self, ws_1=P('Wheel Speed (L) (1)'), ws_2=P('Wheel Speed (L) (2)'),
               ws_3=P('Wheel Speed (L) (3)'), ws_4=P('Wheel Speed (L) (4)')):
        sources = [ws_1, ws_2, ws_3, ws_4]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources, self.offset, self.frequency)


class WheelSpeedRight(DerivedParameterNode):
    '''
    Merge the various recorded wheel speed signals from the right hand bogie.
    '''
    name = 'Wheel Speed (R)'
    align = False
    
    @classmethod
    def can_operate(cls, available):
        return 'Wheel Speed (R) (1)' in available

    def derive(self, ws_1=P('Wheel Speed (R) (1)'), ws_2=P('Wheel Speed (R) (2)'),
               ws_3=P('Wheel Speed (R) (3)'), ws_4=P('Wheel Speed (R) (4)')):
        sources = [ws_1, ws_2, ws_3, ws_4]
        self.offset = 0.0
        self.frequency = 4.0
        self.array = blend_parameters(sources, self.offset, self.frequency)


class WheelSpeed(DerivedParameterNode):
    '''
    Merge Left and Right wheel speeds.
    
    Q: Should wheel speed Centre (C) be merged too?
    '''
    align = False
    
    def derive(self, ws_l=P('Wheel Speed (L)'), ws_r=P('Wheel Speed (R)')):
        self.array, self.frequency, self.offset = \
            blend_two_parameters(ws_l, ws_r)


class TrackContinuous(DerivedParameterNode):
    '''
    Magnetic Track Heading Continuous of the Aircraft by adding Drift from track
    to the aircraft Heading.
    '''
    units = 'deg'
    
    def derive(self, heading=P('Heading Continuous'), drift=P('Drift')):
        #Note: drift is to the right of heading, so: Track = Heading + Drift
        self.array = heading.array + drift.array


class Track(DerivedParameterNode):
    '''
    Magnetic Track Heading of the Aircraft by adding Drift from track to the
    aircraft Heading.

    Range 0 to 360
    '''
    units = 'deg'

    def derive(self, track=P('Track Continuous')):
        self.array = track.array % 360


class TrackTrueContinuous(DerivedParameterNode):
    '''
    True Track Heading Continuous of the Aircraft by adding Drift from track to
    the aircraft Heading.
    '''
    units = 'deg'
    
    def derive(self, heading=P('Heading True Continuous'), drift=P('Drift')):
        #Note: drift is to the right of heading, so: Track = Heading + Drift
        self.array = heading.array + drift.array


class TrackTrue(DerivedParameterNode):
    '''
    True Track Heading of the Aircraft by adding Drift from track to the
    aircraft Heading.

    Range 0 to 360
    '''
    units = 'deg'

    def derive(self, track_true=P('Track True Continuous')):
        self.array = track_true.array % 360


class TrackDeviationFromRunway(DerivedParameterNode):
    '''
    Difference from the aircraft's Track angle and that of the Runway
    centreline. Measured during Takeoff and Approach phases.

    Based on Track True angle in order to avoid complications with magnetic
    deviation values recorded at airports. The deviation from runway centre
    line would be the same whether the calculation is based on Magnetic or
    True measurements.
    '''
    # forse offset for approach slice start consistency
    align_frequency = 1
    align_offset = 0

    @classmethod
    def can_operate(cls, available):
        return any_of(('Approach Information', 'FDR Takeoff Runway'), available) \
               and any_of(('Track Continuous', 'Track True Continuous'), available)

    def _track_deviation(self, array, _slice, rwy, magnetic=False):
        if magnetic:
            try:
                # If magnetic heading is being used get magnetic heading
                # of runway
                self.array[_slice] = runway_deviation(
                     array[_slice], heading=rwy['magnetic_heading'])
                return
            except KeyError:
                # If magnetic heading is not know for runway fallback to
                # true heading
                pass
        try:
            self.array[_slice] = runway_deviation(array[_slice], runway=rwy)
        except ValueError:
            # could not determine runway information
            return

    def derive(self, track_true=P('Track True Continuous'),
               track_mag=P('Track Continuous'),
               takeoff=S('Takeoff'),
               to_rwy=A('FDR Takeoff Runway'),
               apps=App('Approach Information')):
        
        if track_true:
            magnetic = False
            track = track_true
        else:
            magnetic = True
            track = track_mag

        self.array = np_ma_masked_zeros_like(track.array)

        for app in apps:
            if not app.runway:
                self.warning("Cannot calculate TrackDeviationFromRunway for "
                             "approach as there is no runway.")
                continue
            self._track_deviation(track.array, app.slice, app.runway, magnetic)

        if to_rwy:
            self._track_deviation(track.array, takeoff[0].slice, to_rwy.value,
                                  magnetic)





class ElevatorActuatorMismatch(DerivedParameterNode):
    '''
    An incident focused attention on mismatch between the elevator actuator
    and surface. This parameter is designed to measure the mismatch which
    should never be large, and from which we may be able to predict actuator
    malfunctions.
    '''
    def derive(self, elevator=P('Elevator'), 
               ap=M('AP Engaged'), 
               fcc=M('FCC Local Limited Master'),
               left=P('Elevator (L) Actuator'), 
               right=P('Elevator (R) Actuator')):
        
        scaling = 1/2.6 # 737 elevator specific at this time
        
        fcc_l = np.ma.where(fcc.array == 'FCC (L)', 1, 0)
        fcc_r = np.ma.where(fcc.array == 'FCC (R)', 1, 0)
        
        amm = actuator_mismatch(ap.array.raw, 
                                fcc_l,
                                fcc_r,
                                left.array,
                                right.array,
                                elevator.array,
                                scaling,
                                self.frequency)
        
        self.array = amm


class VMOLookup(DerivedParameterNode):
    '''
    Maximum operating limit speed.
    '''
    name = 'VMO Lookup'
    units = 'kts'

    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family')):
        return 'Altitude AAL' in available and bool(get_vmo_procedure(
            series=series.value, family=family.value).vmo)

    def derive(self, aal=P('Altitude AAL'), series=A('Series'),
               family=A('Family')):
        proc = get_vmo_procedure(series=series.value, family=family.value)
        if proc:
            self.array = proc.get_vmo_mmo_arrays(aal.array)[0]


class MMOLookup(DerivedParameterNode):
    '''
    Maximum operating limit Mach.
    '''
    name = 'MMO Lookup'
    units = 'Mach'

    @classmethod
    def can_operate(cls, available, series=A('Series'), family=A('Family')):
        return 'Altitude AAL' in available and bool(get_vmo_procedure(
            series=series.value, family=family.value).mmo)

    def derive(self, aal=P('Altitude AAL'), series=A('Series'),
               family=A('Family')):
        proc = get_vmo_procedure(series=series.value, family=family.value)
        if proc:
            self.array = proc.get_vmo_mmo_arrays(aal.array)[1]

