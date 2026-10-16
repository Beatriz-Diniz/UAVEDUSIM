/****************************************************************************
 *
 *   UAVJamSim - Barometer Mitigation Module (implementation)
 * 
 *   Detecta anomalias na altitude implicita na pressao publicada: divergencia
 *   contra o valor esperado por uma ancora entre essa altitude e a altitude
 *   do GPS (referencia independente), calibrada a partir das primeiras
 *   amostras saudaveis e adaptada lentamente depois. O ataque e um offset
 *   constante de altitude, que uma comparacao por taxa de variacao nunca
 *   pegaria (o offset se cancela na diferenca entre amostras): so uma
 *   comparacao por valor absoluto contra essa ancora detecta.
 *
 *   Quando confirmada, reconstroi a pressao a partir da altitude esperada
 *   (ancora + altitude atual do GPS), usando o modelo de atmosfera padrao.
 *   A temperatura publicada nunca precisa ser reconstruida: vem de um
 *   caminho (airspeedCallback) que o ataque de barometro nunca toca.
 *
 ****************************************************************************/

#include "BarometerMitigation.hpp"

#include <cmath>

#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>

void BarometerMitigation::reset()
{
	_baro_baseline_initialized = false;
	_baro_baseline_warmup_count = 0;
	_baro_anchor_offset_m = 0.0;

	_baro_anomaly_active = false;
	_baro_anomaly_start_us = 0;
	_baro_recovery_start_us = 0;
	_baro_mitig_diag_count = 0;

	_baro_landing_active = false;
	_baro_auto_land_sent = false;
	_baro_auto_disarm_sent = false;
	_baro_auto_land_arrival_us = 0;
	_baro_auto_land_last_arrived_us = 0;
	_baro_land_hold_n_m = 0.0;
	_baro_land_hold_e_m = 0.0;
	_baro_sim_agl_ground_count = 0;
}

// ======== MITIGACAO BAROMETRO - INICIO ========
// Deteccao de anomalia na altitude implicita na pressao publicada, sem
// consultar a opcao de ataque: divergencia contra o valor esperado por uma
// ancora entre essa altitude e a altitude do GPS (referencia independente),
// calibrada a partir das primeiras amostras saudaveis. O ataque e um offset
// constante de altitude, que uma comparacao por taxa de variacao nunca
// pegaria (o offset se cancela na diferenca entre amostras): so uma
// comparacao por valor absoluto contra essa ancora detecta.
void BarometerMitigation::detectAndCorrect(bool mitigation_enabled, uint64_t timestamp,
					    bool gps_real_valid, uint64_t gps_real_alt_timestamp, double gps_real_alt,
					    float raw_temperature,
					    float &out_pressure, float &out_temperature)
{
	if (!mitigation_enabled) {
		return;
	}

	constexpr float P0 = 101325.0f;
	constexpr float T0 = 288.15f;
	constexpr float L  = 0.0065f;
	constexpr float EXP = 0.190263f;

	const float P_published = out_pressure;
	const bool pressure_valid =
		PX4_ISFINITE(P_published) && P_published > 1000.0f && P_published < 120000.0f;

	double baro_alt_from_report = NAN;

	if (pressure_valid) {
		baro_alt_from_report =
			(static_cast<double>(T0) / static_cast<double>(L)) *
			(1.0 - pow(static_cast<double>(P_published) / static_cast<double>(P0), static_cast<double>(EXP)));
	}

	const bool gps_alt_recent =
		gps_real_valid &&
		(timestamp >= gps_real_alt_timestamp) &&
		((timestamp - gps_real_alt_timestamp) <= BARO_GPS_ALT_MAX_AGE_US) &&
		PX4_ISFINITE(gps_real_alt);

	double residual = 0.0;
	bool sample_anomalous = false;

	if (gps_alt_recent && pressure_valid && _baro_baseline_initialized) {
		const double expected_alt = _baro_anchor_offset_m + gps_real_alt;
		residual = baro_alt_from_report - expected_alt;
		sample_anomalous = fabs(residual) > BARO_RESIDUAL_THRESHOLD_M;
	}

	if (!sample_anomalous) {

		// Atualiza a ancora apenas com amostras saudaveis. As primeiras
		// amostras (warmup) calibram com um alpha mais agressivo; depois,
		// adapta lentamente (tolera deriva legitima, mas um ataque
		// sustentado nao chega a ser absorvido, pois so entra aqui quando
		// a amostra nao e anomala).
		if (gps_alt_recent && pressure_valid) {
			const double sample_offset = baro_alt_from_report - gps_real_alt;

			if (_baro_baseline_warmup_count == 0) {
				_baro_anchor_offset_m = sample_offset;
				_baro_baseline_warmup_count = 1;

			} else if (_baro_baseline_warmup_count < BARO_BASELINE_WARMUP_SAMPLES) {
				_baro_anchor_offset_m += BARO_BASELINE_WARMUP_ALPHA * (sample_offset - _baro_anchor_offset_m);
				_baro_baseline_warmup_count++;

				if (_baro_baseline_warmup_count >= BARO_BASELINE_WARMUP_SAMPLES) {
					_baro_baseline_initialized = true;
				}

			} else {
				_baro_anchor_offset_m += BARO_BASELINE_ALPHA * (sample_offset - _baro_anchor_offset_m);
			}
		}

		if (_baro_anomaly_active) {
			if (_baro_recovery_start_us == 0) {
				_baro_recovery_start_us = timestamp;

			} else if ((timestamp - _baro_recovery_start_us) >= BARO_RECOVERY_CONFIRM_US) {
				_baro_anomaly_active = false;
				_baro_anomaly_start_us = 0;
				_baro_mitig_diag_count = 0;

				PX4_INFO("\n[Baro-Mitig] Sensor anomaly cleared\n");
			}
		}

	} else {
		_baro_recovery_start_us = 0;

		if (_baro_anomaly_start_us == 0) {
			_baro_anomaly_start_us = timestamp;
		}

		if (!_baro_anomaly_active &&
		    ((timestamp - _baro_anomaly_start_us) >= BARO_ANOMALY_CONFIRM_US)) {
			_baro_anomaly_active = true;

			PX4_WARN("\n[Baro-Mitig] Sensor anomaly detected | alt=%.2f m | residual=%.2f m\n",
				 baro_alt_from_report, residual);
		}

		// Reconstroi a pressao a partir da altitude esperada (ancora +
		// altitude atual do GPS), usando o mesmo modelo de atmosfera
		// padrao ja usado para calcular altitude a partir de pressao. A
		// temperatura publicada nunca precisa ser reconstruida: vem de um
		// caminho (airspeedCallback) que o ataque de barometro nunca toca.
		if (_baro_anomaly_active && gps_alt_recent && _baro_baseline_initialized) {
			const double expected_alt = _baro_anchor_offset_m + gps_real_alt;
			const double corrected_pressure =
				static_cast<double>(P0) *
				pow(1.0 - (static_cast<double>(L) * expected_alt) / static_cast<double>(T0),
				    1.0 / static_cast<double>(EXP));

			if (PX4_ISFINITE(corrected_pressure) && corrected_pressure > 1000.0 && corrected_pressure < 120000.0) {
				out_pressure = static_cast<float>(corrected_pressure);
			}

			out_temperature = raw_temperature;
		}

		if (_baro_anomaly_active && (_baro_mitig_diag_count < BARO_MITIG_DIAG_MAX_COUNT)) {
			static uint64_t baro_mitig_diag_last_us = 0;

			if ((timestamp - baro_mitig_diag_last_us) > 1000000ULL) {
				baro_mitig_diag_last_us = timestamp;
				_baro_mitig_diag_count++;

				PX4_INFO("\n[Baro-Mitig] Status | pressure=%.1f Pa | residual=%.2f m | gps_alt=%s\n",
					 static_cast<double>(out_pressure),
					 residual,
					 gps_alt_recent ? "ok" : "stale");

				if (_baro_mitig_diag_count == BARO_MITIG_DIAG_MAX_COUNT) {
					PX4_WARN("\n[Baro-Mitig] Check: listener sensor_baro\n");
				}
			}
		}
	}
}
// ======== MITIGACAO BAROMETRO - FIM ========

// ======== MITIGACAO DE POUSO BAROMETRO - INICIO ========
// Pouso proprio de barometro: usa LPOS como unica fonte de posicao confiavel.
void BarometerMitigation::checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
					 bool lpos_xy_valid, uint64_t lpos_timestamp,
					 double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	if (!_baro_anomaly_active || _baro_auto_land_sent || !pos_ref.isInitialized()) {
		_baro_auto_land_arrival_us = 0;
		_baro_auto_land_last_arrived_us = 0;
		return;
	}

	if (!lpos_xy_valid || (timestamp - lpos_timestamp) > 500000ULL) {
		_baro_auto_land_arrival_us = 0;
		_baro_auto_land_last_arrived_us = 0;
		return;
	}

	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		_baro_auto_land_arrival_us = 0;
		_baro_auto_land_last_arrived_us = 0;
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
		_baro_auto_land_arrival_us = 0;
		_baro_auto_land_last_arrived_us = 0;
		return;
	}

	_baro_auto_land_last_arrived_us = timestamp;

	if (_baro_auto_land_arrival_us == 0) {
		_baro_auto_land_arrival_us = timestamp;

		_baro_land_hold_n_m = lpos_n_m;
		_baro_land_hold_e_m = lpos_e_m;

		PX4_INFO("\n[Baro-Land] Destination reached | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 static_cast<double>(horizontal_error),
			 static_cast<double>(lpos_ground_speed),
			 static_cast<double>(AUTO_LAND_STABLE_US) / 1e6);

		return;
	}

	const uint64_t preland_elapsed_us = timestamp - _baro_auto_land_arrival_us;

	if (preland_elapsed_us < AUTO_LAND_STABLE_US) {
		return;
	}

	_baro_landing_active = true;
	_baro_auto_land_sent = true;

	publishLandCommand(timestamp, pos_ref);
}

// Confirma contato com o solo por multiplas fontes (land detector do PX4,
// sensor de distancia, altura simulada) antes de desarmar. A anomalia
// original nunca e usada como criterio aqui.
void BarometerMitigation::checkAutoDisarm(uint64_t timestamp,
					   bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					   bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					   bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					   float lpos_ground_speed)
{
	if (!_baro_landing_active || !_baro_auto_land_sent || _baro_auto_disarm_sent) {
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
			_baro_sim_agl_ground_count++;
		} else {
			_baro_sim_agl_ground_count = 0;
		}
	} else {
		_baro_sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(_baro_sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

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
		PX4_WARN("\n[Baro-Land] PX4 landing detector confirmed landed - normal disarm\n");

	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[Baro-Land] PX4 landing detector reported ground contact - safety disarm\n");

	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[Baro-Land] Downward distance sensor reported ground contact | dist=%.2f m - safety disarm\n",
			 static_cast<double>(ground_distance_m));

	} else {
		PX4_WARN("\n[Baro-Land] Simulated ground proximity reached | agl=%.2f m - safety disarm\n",
			 static_cast<double>(sim_agl_m));
	}

	publishDisarmCommand(timestamp, force_disarm);
	_baro_auto_disarm_sent = true;
	_baro_auto_land_sent = true;
	_baro_landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void BarometerMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref)
{
	double land_lat = 0.0;
	double land_lon = 0.0;
	pos_ref.reproject(
		static_cast<float>(_baro_land_hold_n_m),
		static_cast<float>(_baro_land_hold_e_m),
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

	PX4_WARN("\n[Baro-Land] Controlled landing started | hold N=%.1f E=%.1f | lat=%.7f lon=%.7f\n",
		 _baro_land_hold_n_m,
		 _baro_land_hold_e_m,
		 land_lat,
		 land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void BarometerMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm)
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

	PX4_WARN("\n[Baro-Land] %s disarm command sent after ground contact confirmation.\n",
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO BAROMETRO - FIM ========