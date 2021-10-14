from common.numpy_fast import clip, interp
from opendbc.can.packer import CANPacker
from selfdrive.car.tesla.teslacan import TeslaCAN
from selfdrive.car.tesla.values import DBC, CANBUS, CarControllerParams
from selfdrive.controls.lib.longcontrol import ACCEL_MIN_ISO, ACCEL_MAX_ISO

class CarController():
  def __init__(self, dbc_name, CP, VM):
    self.CP = CP
    self.last_angle = 0
    self.packer = CANPacker(dbc_name)
    self.longitudinal_packer = CANPacker(DBC[CP.carFingerprint]['pt'])
    self.tesla_can = TeslaCAN(self.packer, self.longitudinal_packer)

  def update(self, enabled, CS, frame, actuators, cruise_cancel):
    can_sends = []

    # Temp disable steering on a hands_on_fault, and allow for user override
    hands_on_fault = (CS.steer_warning == "EAC_ERROR_HANDS_ON" and CS.hands_on_level >= 3)
    lkas_enabled = enabled and (not hands_on_fault)

    if lkas_enabled:
      apply_angle = actuators.steeringAngleDeg

      # Angular rate limit based on speed
      steer_up = (self.last_angle * apply_angle > 0. and abs(apply_angle) > abs(self.last_angle))
      rate_limit = CarControllerParams.RATE_LIMIT_UP if steer_up else CarControllerParams.RATE_LIMIT_DOWN
      max_angle_diff = interp(CS.out.vEgo, rate_limit.speed_points, rate_limit.max_angle_diff_points)
      apply_angle = clip(apply_angle, (self.last_angle - max_angle_diff), (self.last_angle + max_angle_diff))

      # To not fault the EPS
      apply_angle = clip(apply_angle, (CS.out.steeringAngleDeg - 20), (CS.out.steeringAngleDeg + 20))
    else:
      apply_angle = CS.out.steeringAngleDeg

    self.last_angle = apply_angle
    can_sends.append(self.tesla_can.create_steering_control(apply_angle, lkas_enabled, frame))

    # Longitudinal control
    if self.CP.openpilotLongitudinalControl:
      target_accel = actuators.accel
      target_speed = CS.out.vEgo + (target_accel * CarControllerParams.ACCEL_TO_SPEED_MULTIPLIER)
      max_accel = ACCEL_MAX_ISO if target_accel < 0 else target_accel
      min_accel = ACCEL_MIN_ISO if target_accel > 0 else target_accel

      can_sends.append(self.tesla_can.create_longitudinal_command(lkas_enabled, target_speed, min_accel, max_accel, frame))

    # Cancel on user steering override, since there is no steering torque blending
    if hands_on_fault:
      cruise_cancel = True

    # Cancel when openpilot is not enabled anymore
    if not enabled and bool(CS.out.cruiseState.enabled):
      cruise_cancel = True

    if ((frame % 10) == 0 and cruise_cancel):
      # Spam every possible counter value, otherwise it might not be accepted
      for counter in range(16):
        can_sends.append(self.tesla_can.create_action_request(CS.msg_stw_actn_req, cruise_cancel, CANBUS.chassis, counter))
        can_sends.append(self.tesla_can.create_action_request(CS.msg_stw_actn_req, cruise_cancel, CANBUS.autopilot_chassis, counter))

    # TODO: HUD control

    return can_sends
