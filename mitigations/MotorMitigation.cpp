/****************************************************************************
 *
 *   UAVJamSim - Motor Mitigation Module (implementation)
 *
 ****************************************************************************/

#include "MotorMitigation.hpp"

#include <cmath>

#include <drivers/drv_hrt.h>
#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>

void MotorMitigation::reset()
{
	_motor_landing_active = false;
	_motor_auto_land_sent = false;
	_motor_auto_disarm_sent = false;
	_motor_auto_land_arrival_us = 0;
	_motor_auto_land_last_arrived_us = 0;
	_motor_land_hold_n_m = 0.0;
	_motor_land_hold_e_m = 0.0;
	_motor_sim_agl_ground_count = 0;
}

// Liga/desliga a deteccao. Chamado a partir do topico unico de mitigacao do
// GZBridge (msg.x==1 ativa, qualquer outro valor desativa).
void MotorMitigation::setEnabled(bool enabled)
{
	_motor_mitigation_enabled = enabled;
	_motor_anomaly_active.store(false);
	_motor_anomaly_start_us = 0;
	_motor_recovery_start_us = 0;
}

// ======== MITIGACAO MOTOR - INICIO ========
// Deteccao por implausibilidade fisica do comando prestes a ser enviado,
// sem consultar a opcao de ataque:
//  (a) todos os motores comandados a empuxo proximo de zero enquanto o
//      veiculo esta armado e nao pousado;
//  (b) um motor com saida muito diferente da media dos demais, quando a
//      media indica empuxo relevante.
// (b) roda independente do estado de armado/pousado: um motor girando
// muito diferente dos demais e implausivel em qualquer estado, inclusive
// desarmado (o ataque nao respeita o estado de armado ao forcar o valor).
void MotorMitigation::detectAndCorrect(uint16_t outputs[], const uint16_t legitimate_outputs[], unsigned active_output_count)
{
	bool anomaly_this_cycle = false;

	if (_motor_mitigation_enabled) {

		float sum_outputs = 0.0f;

		for (unsigned i = 0; i < active_output_count; i++) {
			sum_outputs += static_cast<float>(outputs[i]);
		}

		const float mean_output = sum_outputs / static_cast<float>(active_output_count);
		const bool all_near_zero = mean_output < MOTOR_MIN_ARMED_OUTPUT;

		if (all_near_zero) {
			actuator_armed_s armed{};
			const bool armed_available = _actuator_armed_detect_sub.copy(&armed);
			const bool vehicle_armed = armed_available && armed.armed;

			vehicle_land_detected_s land_detected{};
			const bool land_detector_available = _vehicle_land_detected_detect_sub.copy(&land_detected);
			const bool vehicle_not_landed = land_detector_available && !land_detected.landed;

			if (vehicle_armed && vehicle_not_landed) {
				// Restaura o comando legitimo em todos os motores ativos.
				for (unsigned i = 0; i < active_output_count; i++) {
					outputs[i] = legitimate_outputs[i];
				}

				anomaly_this_cycle = true;
			}

		} else {
			const float asymmetry_threshold = fmaxf(
					MOTOR_ASYMMETRY_MIN_ABS,
					mean_output * MOTOR_ASYMMETRY_FRACTION);

			for (unsigned i = 0; i < active_output_count; i++) {
				const float deviation = fabsf(static_cast<float>(outputs[i]) - mean_output);

				if (deviation > asymmetry_threshold) {
					outputs[i] = legitimate_outputs[i];
					anomaly_this_cycle = true;
				}
			}

			if (_motor_anomaly_active.load(std::memory_order_relaxed)) {
				static uint64_t motor_mitig_diag_last_us = 0;
				const uint64_t diag_now_us = hrt_absolute_time();

				if ((diag_now_us - motor_mitig_diag_last_us) > 1000000ULL) {
					motor_mitig_diag_last_us = diag_now_us;

					PX4_INFO("\n[Motor-Mitig] Status | mean=%.1f threshold=%.1f\n",
						 static_cast<double>(mean_output),
						 static_cast<double>(asymmetry_threshold));
				}
			}
		}
	}

	const uint64_t motor_mitig_timestamp = hrt_absolute_time();

	if (anomaly_this_cycle) {
		_motor_recovery_start_us = 0;

		if (_motor_anomaly_start_us == 0) {
			_motor_anomaly_start_us = motor_mitig_timestamp;
		}

		if (!_motor_anomaly_active.load(std::memory_order_relaxed) &&
		    ((motor_mitig_timestamp - _motor_anomaly_start_us) >= MOTOR_ANOMALY_CONFIRM_US)) {
			_motor_anomaly_active.store(true, std::memory_order_relaxed);
			PX4_WARN("\n[Motor-Mitig] Actuator command anomaly detected and rejected\n");
		}

	} else {
		_motor_anomaly_start_us = 0;

		if (_motor_anomaly_active.load(std::memory_order_relaxed)) {
			if (_motor_recovery_start_us == 0) {
				_motor_recovery_start_us = motor_mitig_timestamp;

			} else if ((motor_mitig_timestamp - _motor_recovery_start_us) >= MOTOR_RECOVERY_CONFIRM_US) {
				_motor_anomaly_active.store(false, std::memory_order_relaxed);
				_motor_recovery_start_us = 0;
				PX4_INFO("\n[Motor-Mitig] Actuator command anomaly cleared\n");
			}
		}
	}
}
// ======== MITIGACAO MOTOR - FIM ========

// ======== MITIGACAO DE POUSO MOTOR - INICIO ========
// Pouso proprio de motor: usa LPOS como unica fonte de posicao confiavel.
void MotorMitigation::checkAutoLand(uint64_t timestamp, MapProjection &pos_ref, bool anomaly_active,
				     bool lpos_xy_valid, uint64_t lpos_timestamp,
				     double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	if (!anomaly_active || _motor_auto_land_sent || !pos_ref.isInitialized()) {
		_motor_auto_land_arrival_us = 0;
		_motor_auto_land_last_arrived_us = 0;
		return;
	}

	if (!lpos_xy_valid || (timestamp - lpos_timestamp) > 500000ULL) {
		_motor_auto_land_arrival_us = 0;
		_motor_auto_land_last_arrived_us = 0;
		return;
	}

	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		_motor_auto_land_arrival_us = 0;
		_motor_auto_land_last_arrived_us = 0;
		return;
	}

	float dest_n_m = 0.0f;
	float dest_e_m = 0.0f;
	pos_ref.project(triplet.current.lat, triplet.current.lon, dest_n_m, dest_e_m);

	const double dn = lpos_n_m - static_cast<double>(dest_n_m);
	const double de = lpos_e_m - static_cast<double>(dest_e_m);
	const float horizontal_error = static_cast<float>(sqrt(dn * dn + de * de));

	const bool arrived =
		(horizontal_error <= AUTO_LAND_ANOMALY_DEST_RADIUS_M) &&
		(lpos_ground_speed <= AUTO_LAND_ANOMALY_MAX_SPEED_M_S);

	if (!arrived) {
		_motor_auto_land_arrival_us = 0;
		_motor_auto_land_last_arrived_us = 0;
		return;
	}

	_motor_auto_land_last_arrived_us = timestamp;

	if (_motor_auto_land_arrival_us == 0) {
		_motor_auto_land_arrival_us = timestamp;

		_motor_land_hold_n_m = lpos_n_m;
		_motor_land_hold_e_m = lpos_e_m;

		PX4_INFO("\n[Motor-Land] Destination reached | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 static_cast<double>(horizontal_error),
			 static_cast<double>(lpos_ground_speed),
			 static_cast<double>(AUTO_LAND_STABLE_US) / 1e6);

		return;
	}

	const uint64_t preland_elapsed_us = timestamp - _motor_auto_land_arrival_us;

	if (preland_elapsed_us < AUTO_LAND_STABLE_US) {
		return;
	}

	_motor_landing_active = true;
	_motor_auto_land_sent = true;

	publishLandCommand(timestamp, pos_ref);
}

// Confirma contato com o solo por multiplas fontes (land detector do PX4,
// sensor de distancia, altura simulada) antes de desarmar. A anomalia
// original nunca e usada como criterio aqui.
void MotorMitigation::checkAutoDisarm(uint64_t timestamp,
				       bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				       bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				       bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				       float lpos_ground_speed)
{
	if (!_motor_landing_active || !_motor_auto_land_sent || _motor_auto_disarm_sent) {
		return;
	}

	vehicle_land_detected_s land_detected{};
	const bool land_detector_available = _vehicle_land_detected_sub.copy(&land_detected);

	const bool land_detector_recent =
		land_detector_available &&
		(land_detected.timestamp > 0) &&
		(timestamp >= land_detected.timestamp) &&
		((timestamp - land_detected.timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US);

	const bool ground_contact_by_px4_landed =
		land_detector_recent && land_detected.landed;

	const bool ground_contact_by_px4_early =
		land_detector_recent &&
		(land_detected.ground_contact || land_detected.maybe_landed);

	const bool ground_sensor_recent =
		ground_distance_valid &&
		((timestamp - ground_distance_timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US);

	const bool ground_contact_by_sensor =
		ground_sensor_recent &&
		PX4_ISFINITE(ground_distance_m) &&
		(ground_distance_m <= AUTO_LAND_GROUND_DISARM_DIST_M);

	const bool sim_ground_recent =
		sim_agl_valid &&
		(timestamp >= sim_agl_timestamp) &&
		((timestamp - sim_agl_timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US) &&
		PX4_ISFINITE(sim_agl_m);

	if (sim_ground_recent) {
		if (sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) {
			_motor_sim_agl_ground_count++;
		} else {
			_motor_sim_agl_ground_count = 0;
		}
	} else {
		_motor_sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(_motor_sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

	if (!ground_contact_by_px4_landed &&
	    !ground_contact_by_px4_early &&
	    !ground_contact_by_sensor &&
	    !ground_contact_by_sim_agl) {
		return;
	}

	const bool force_disarm = !ground_contact_by_px4_landed;

	const bool disarm_att_recent =
		sim_att_valid &&
		(timestamp >= sim_att_timestamp) &&
		((timestamp - sim_att_timestamp) <= 500000ULL);

	const bool forced_disarm_speed_ok = (lpos_ground_speed <= 0.15f);

	const bool forced_disarm_att_ok =
		!disarm_att_recent ||
		((fabsf(sim_roll_rad) <= AUTO_LAND_MAX_ROLL_RAD) &&
		 (fabsf(sim_pitch_rad) <= AUTO_LAND_MAX_PITCH_RAD));

	if (force_disarm && (!forced_disarm_speed_ok || !forced_disarm_att_ok)) {
		return;
	}

	if (ground_contact_by_px4_landed) {
		PX4_WARN("\n[Motor-Land] PX4 landing detector confirmed landed - normal disarm\n");

	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[Motor-Land] PX4 landing detector reported ground contact - safety disarm\n");

	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[Motor-Land] Downward distance sensor reported ground contact | dist=%.2f m - safety disarm\n",
			 static_cast<double>(ground_distance_m));

	} else {
		PX4_WARN("\n[Motor-Land] Simulated ground proximity reached | agl=%.2f m - safety disarm\n",
			 static_cast<double>(sim_agl_m));
	}

	publishDisarmCommand(timestamp, force_disarm);
	_motor_auto_disarm_sent = true;
	_motor_auto_land_sent = true;
	_motor_landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void MotorMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref)
{
	double land_lat = 0.0;
	double land_lon = 0.0;
	pos_ref.reproject(
		static_cast<float>(_motor_land_hold_n_m),
		static_cast<float>(_motor_land_hold_e_m),
		land_lat,
		land_lon);

	vehicle_command_s cmd{};
	cmd.timestamp = timestamp;
	cmd.param1 = 0.0f;
	cmd.param2 = 0.0f;
	cmd.param3 = 0.0f;
	cmd.param4 = NAN;
	cmd.param5 = static_cast<float>(land_lat);
	cmd.param6 = static_cast<float>(land_lon);
	cmd.param7 = NAN;

	cmd.command = vehicle_command_s::VEHICLE_CMD_NAV_LAND;
	cmd.target_system = 1;
	cmd.target_component = 1;
	cmd.source_system = 1;
	cmd.source_component = 1;
	cmd.confirmation = 0;
	cmd.from_external = false;

	_vehicle_command_pub.publish(cmd);

	PX4_WARN("\n[Motor-Land] Controlled landing started | hold N=%.1f E=%.1f | lat=%.7f lon=%.7f\n",
		 _motor_land_hold_n_m,
		 _motor_land_hold_e_m,
		 land_lat,
		 land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void MotorMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm)
{
	vehicle_command_s cmd{};
	cmd.timestamp = timestamp;
	cmd.param1 = 0.0f;
	cmd.param2 = force_disarm ? 21196.0f : 0.0f;
	cmd.param3 = 0.0f;
	cmd.param4 = 0.0f;
	cmd.param5 = 0.0f;
	cmd.param6 = 0.0f;
	cmd.param7 = 0.0f;
	cmd.command = vehicle_command_s::VEHICLE_CMD_COMPONENT_ARM_DISARM;
	cmd.target_system = 1;
	cmd.target_component = 1;
	cmd.source_system = 1;
	cmd.source_component = 1;
	cmd.confirmation = 0;
	cmd.from_external = false;

	_vehicle_command_pub.publish(cmd);

	PX4_WARN("\n[Motor-Land] %s disarm command sent after ground contact confirmation.\n",
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO MOTOR - FIM ========