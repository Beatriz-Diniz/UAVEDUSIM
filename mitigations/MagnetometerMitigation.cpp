/****************************************************************************
 *
 *   UAVJamSim - Magnetometer Mitigation Module (implementation)
 *
 *   Detecta anomalias no magnetometro combinando dois sinais:
 *     (a) magnitude do campo horizontal fora de uma faixa fisica plausivel;
 *     (b) heading implicito no magnetometro divergindo do heading da VIO
 *         (referencia independente) em relacao a um offset calibrado a
 *         partir das primeiras amostras saudaveis e adaptado lentamente depois.
 *
 *   So confia no calculo de heading quando o veiculo esta proximo do
 *   nivelado (a convencao de eixos do magnetometro simulado nao e garantida
 *   como a mesma FRD usada por accel/gyro, entao nao arriscamos compensar
 *   inclinacao via rotacao).
 *
 *   Quando confirmada, reconstroi a direcao a partir do heading esperado
 *   (reflexao do heading da VIO mais o offset calibrado), preservando a
 *   magnitude horizontal ainda confiavel do proprio sinal atacado.
 *
 *   Mantem seu proprio cache de atitude da VIO (heading/roll/pitch),
 *   alimentado por updateVioAttitude() a partir de GZBridge::odometryCallback.
 * 
 ****************************************************************************/

#include "MagnetometerMitigation.hpp"

#include <cmath>

#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>

void MagnetometerMitigation::reset()
{
	// O cache de atitude da VIO (_vio_*) nao e resetado aqui: nao depende
	// do estado da mitigacao, so reflete a leitura mais recente do sensor,
	// igual outros caches de referencia do projeto (ex.: camera de
	// profundidade em LidarMitigation).
	_mag_baseline_initialized = false;
	_mag_baseline_warmup_count = 0;
	_mag_heading_offset_rad = 0.0;

	_mag_anomaly_active = false;
	_mag_anomaly_start_us = 0;
	_mag_recovery_start_us = 0;

	_mag_mitig_diag_count = 0;

	_mag_landing_active = false;
	_mag_auto_land_sent = false;
	_mag_auto_disarm_sent = false;
	_mag_auto_land_arrival_us = 0;
	_mag_auto_land_last_arrived_us = 0;
	_mag_land_hold_n_m = 0.0;
	_mag_land_hold_e_m = 0.0;
	_mag_sim_agl_ground_count = 0;
}

// ======== MITIGACAO MAGNETOMETRO - INICIO ========
// Deteccao de anomalia no sinal publicado do sensor, sem consultar a opcao
// de ataque:
//  (a) magnitude do campo horizontal fora de uma faixa fisica plausivel;
//  (b) heading implicito no magnetometro divergindo do heading da VIO
//      (referencia independente) em relacao a um offset calibrado a
//      partir das primeiras amostras saudaveis e adaptado lentamente depois.
// So confia no calculo de heading quando o veiculo esta proximo do
// nivelado (roll/pitch da VIO abaixo de MAG_MAX_TILT_RAD): a convencao de
// eixos do magnetometro simulado nao e garantida como a mesma FRD usada
// por accel/gyro, entao nao arriscamos compensar inclinacao via rotacao.
// Nao exige que o veiculo ja tenha armado/voado: o heading e valido
// (e ate mais facil de validar, ja que vio_near_level tende a ser
// verdadeiro parado no chao) mesmo antes da decolagem, e o ataque deve
// ser detectavel nesse estado.
void MagnetometerMitigation::updateVioAttitude(uint64_t timestamp, float heading_rad, float roll_rad, float pitch_rad)
{
	_vio_heading_rad = heading_rad;
	_vio_roll_rad = roll_rad;
	_vio_pitch_rad = pitch_rad;
	_vio_heading_valid = true;
	_vio_heading_us = timestamp;
}

void MagnetometerMitigation::detectAndCorrect(bool mitigation_enabled, uint64_t timestamp,
					       float &report_x, float &report_y)
{
	if (!mitigation_enabled) {
		return;
	}

	const double horiz_mag = sqrt(
					     static_cast<double>(report_x) * static_cast<double>(report_x) +
					     static_cast<double>(report_y) * static_cast<double>(report_y));

	const bool horiz_out_of_bounds =
		!PX4_ISFINITE(horiz_mag) ||
		(horiz_mag < MAG_FIELD_HORIZ_MIN_G) ||
		(horiz_mag > MAG_FIELD_HORIZ_MAX_G);

	const bool vio_att_recent =
		_vio_heading_valid &&
		(timestamp >= _vio_heading_us) &&
		((timestamp - _vio_heading_us) <= MAG_VIO_MAX_AGE_US);

	const bool vio_near_level =
		vio_att_recent &&
		(fabsf(_vio_roll_rad) < MAG_MAX_TILT_RAD) &&
		(fabsf(_vio_pitch_rad) < MAG_MAX_TILT_RAD);

	const bool heading_ref_valid = vio_near_level && (horiz_mag > 1e-9);

	double mag_heading = 0.0;
	double offset = 0.0;
	double heading_residual = 0.0;
	bool heading_anomaly = false;

	if (heading_ref_valid) {
		mag_heading = atan2(static_cast<double>(report_y), static_cast<double>(report_x));

		// A relacao real entre o heading implicito no magnetometro e o
		// heading da VIO e uma reflexao (mag_heading ~= -vio_heading + c),
		// nao uma simples rotacao (mag_heading ~= vio_heading + c). Isso
		// vem do mapeamento de eixos do magnetometro simulado, que tem
		// determinante -1 (troca e inverte os dois eixos horizontais).
		offset = atan2(sin(mag_heading + static_cast<double>(_vio_heading_rad)),
			       cos(mag_heading + static_cast<double>(_vio_heading_rad)));

		if (_mag_baseline_initialized) {
			heading_residual = atan2(sin(offset - _mag_heading_offset_rad), cos(offset - _mag_heading_offset_rad));
			heading_anomaly = fabs(heading_residual) > MAG_HEADING_ERROR_THRESHOLD_RAD;
		}
	}

	const bool sample_anomalous = horiz_out_of_bounds || heading_anomaly;

	// Severo (confirmacao rapida) somente para violacao fisica dura. Um
	// desacordo de heading, por maior que seja, pode vir de interferencia
	// magnetica transitoria durante manobra (curva com tracao diferencial
	// entre motores), entao sempre passa pela janela de confirmacao longa
	// (MAG_ANOMALY_CONFIRM_US) em vez do caminho rapido.
	const bool sample_severe = horiz_out_of_bounds;

	if (!sample_anomalous) {

		// Atualiza a linha de base apenas com amostras saudaveis. As
		// primeiras amostras (warmup) calibram o offset com um alpha mais
		// agressivo, para nao travar numa unica amostra ruidosa logo no
		// inicio do voo real; depois disso, adapta lentamente (tolera
		// deriva legitima do sensor, mas um ataque sustentado nao chega a
		// ser absorvido, pois so entra aqui quando a amostra nao e anomala).
		if (heading_ref_valid) {
			if (_mag_baseline_warmup_count == 0) {
				_mag_heading_offset_rad = offset;
				_mag_baseline_warmup_count = 1;

			} else if (_mag_baseline_warmup_count < MAG_BASELINE_WARMUP_SAMPLES) {
				const double delta = atan2(sin(offset - _mag_heading_offset_rad), cos(offset - _mag_heading_offset_rad));
				_mag_heading_offset_rad = atan2(
							sin(_mag_heading_offset_rad + MAG_BASELINE_WARMUP_ALPHA * delta),
							cos(_mag_heading_offset_rad + MAG_BASELINE_WARMUP_ALPHA * delta));
				_mag_baseline_warmup_count++;

				if (_mag_baseline_warmup_count >= MAG_BASELINE_WARMUP_SAMPLES) {
					_mag_baseline_initialized = true;
				}

			} else {
				const double delta = atan2(sin(offset - _mag_heading_offset_rad), cos(offset - _mag_heading_offset_rad));
				_mag_heading_offset_rad = atan2(
							sin(_mag_heading_offset_rad + MAG_BASELINE_ALPHA * delta),
							cos(_mag_heading_offset_rad + MAG_BASELINE_ALPHA * delta));
			}
		}

		if (_mag_anomaly_active) {
			if (_mag_recovery_start_us == 0) {
				_mag_recovery_start_us = timestamp;

			} else if ((timestamp - _mag_recovery_start_us) >= MAG_RECOVERY_CONFIRM_US) {
				_mag_anomaly_active = false;
				_mag_anomaly_start_us = 0;
				_mag_mitig_diag_count = 0;

				PX4_INFO("\n[Mag-Mitig] Sensor anomaly cleared\n");
			}
		}

	} else {
		_mag_recovery_start_us = 0;

		if (_mag_anomaly_start_us == 0) {
			_mag_anomaly_start_us = timestamp;
		}

		const uint64_t mag_confirm_required_us =
			sample_severe ? MAG_ANOMALY_CONFIRM_SEVERE_US : MAG_ANOMALY_CONFIRM_US;

		if (!_mag_anomaly_active &&
		    ((timestamp - _mag_anomaly_start_us) >= mag_confirm_required_us)) {
			_mag_anomaly_active = true;

			PX4_WARN("\n[Mag-Mitig] Sensor anomaly detected | heading_residual=%.1f deg | mag_heading=%.1f deg | vio_heading=%.1f deg | offset=%.1f deg | baseline=%.1f deg | bounds=%s\n",
				 math::degrees(heading_residual),
				 math::degrees(mag_heading),
				 static_cast<double>(math::degrees(_vio_heading_rad)),
				 math::degrees(offset),
				 math::degrees(_mag_heading_offset_rad),
				 horiz_out_of_bounds ? "out" : "ok");
		}

		// Reconstroi a direcao a partir do heading esperado (reflexao do
		// heading da VIO mais o offset calibrado, mesmo modelo da
		// deteccao), preservando a magnitude horizontal ainda confiavel
		// do proprio sinal atacado. So roda quando a ultima referencia de
		// heading valida foi calculada em voo nivelado (heading_ref_valid
		// so fica true nessas condicoes).
		if (_mag_anomaly_active && heading_ref_valid) {
			const double expected_heading = atan2(
					sin(_mag_heading_offset_rad - static_cast<double>(_vio_heading_rad)),
					cos(_mag_heading_offset_rad - static_cast<double>(_vio_heading_rad)));

			report_x = static_cast<float>(horiz_mag * cos(expected_heading));
			report_y = static_cast<float>(horiz_mag * sin(expected_heading));
		}

		if (_mag_anomaly_active && (_mag_mitig_diag_count < MAG_MITIG_DIAG_MAX_COUNT)) {
			static uint64_t mag_mitig_diag_last_us = 0;

			if ((timestamp - mag_mitig_diag_last_us) > 1000000ULL) {
				mag_mitig_diag_last_us = timestamp;
				_mag_mitig_diag_count++;

				PX4_INFO("\n[Mag-Mitig] Status | heading_residual=%.1f deg | mag_heading=%.1f deg | vio_heading=%.1f deg | horiz_mag=%.2e G | vio_age=%.0f ms\n",
					 math::degrees(heading_residual),
					 math::degrees(mag_heading),
					 static_cast<double>(math::degrees(_vio_heading_rad)),
					 horiz_mag,
					 _vio_heading_valid ? static_cast<double>(timestamp - _vio_heading_us) / 1000.0 : -1.0);

				if (_mag_mitig_diag_count == MAG_MITIG_DIAG_MAX_COUNT) {
					PX4_WARN("\n[Mag-Mitig] Check: listener vehicle_magnetometer\n");
				}
			}
		}
	}
}
// ======== MITIGACAO MAGNETOMETRO - FIM ========

// ======== MITIGACAO DE POUSO MAGNETOMETRO - INICIO ========
// Pouso proprio de magnetometro: usa LPOS como unica fonte de posicao
// confiavel (mesmo padrao adotado para IMU).
void MagnetometerMitigation::checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
					    bool lpos_xy_valid, uint64_t lpos_timestamp,
					    double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	if (!_mag_anomaly_active || _mag_auto_land_sent || !pos_ref.isInitialized()) {
		_mag_auto_land_arrival_us = 0;
		_mag_auto_land_last_arrived_us = 0;
		return;
	}

	if (!lpos_xy_valid || (timestamp - lpos_timestamp) > 500000ULL) {
		_mag_auto_land_arrival_us = 0;
		_mag_auto_land_last_arrived_us = 0;
		return;
	}

	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		_mag_auto_land_arrival_us = 0;
		_mag_auto_land_last_arrived_us = 0;
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
		_mag_auto_land_arrival_us = 0;
		_mag_auto_land_last_arrived_us = 0;
		return;
	}

	_mag_auto_land_last_arrived_us = timestamp;

	if (_mag_auto_land_arrival_us == 0) {
		_mag_auto_land_arrival_us = timestamp;

		_mag_land_hold_n_m = lpos_n_m;
		_mag_land_hold_e_m = lpos_e_m;

		PX4_INFO("\n[Mag-Land] Destination reached | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 static_cast<double>(horizontal_error),
			 static_cast<double>(lpos_ground_speed),
			 static_cast<double>(AUTO_LAND_STABLE_US) / 1e6);

		return;
	}

	const uint64_t preland_elapsed_us = timestamp - _mag_auto_land_arrival_us;

	if (preland_elapsed_us < AUTO_LAND_STABLE_US) {
		return;
	}

	_mag_landing_active = true;
	_mag_auto_land_sent = true;

	publishLandCommand(timestamp, pos_ref);
}

// Confirma contato com o solo por multiplas fontes (land detector do PX4,
// sensor de distancia, altura simulada) antes de desarmar. A anomalia
// original nunca e usada como criterio aqui.
void MagnetometerMitigation::checkAutoDisarm(uint64_t timestamp,
					      bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					      bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					      bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					      float lpos_ground_speed)
{
	if (!_mag_landing_active || !_mag_auto_land_sent || _mag_auto_disarm_sent) {
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
			_mag_sim_agl_ground_count++;
		} else {
			_mag_sim_agl_ground_count = 0;
		}
	} else {
		_mag_sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(_mag_sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

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
		PX4_WARN("\n[Mag-Land] PX4 landing detector confirmed landed - normal disarm\n");

	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[Mag-Land] PX4 landing detector reported ground contact - safety disarm\n");

	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[Mag-Land] Downward distance sensor reported ground contact | dist=%.2f m - safety disarm\n",
			 static_cast<double>(ground_distance_m));

	} else {
		PX4_WARN("\n[Mag-Land] Simulated ground proximity reached | agl=%.2f m - safety disarm\n",
			 static_cast<double>(sim_agl_m));
	}

	publishDisarmCommand(timestamp, force_disarm);
	_mag_auto_disarm_sent = true;
	_mag_auto_land_sent = true;
	_mag_landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void MagnetometerMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref)
{
	double land_lat = 0.0;
	double land_lon = 0.0;
	pos_ref.reproject(
		static_cast<float>(_mag_land_hold_n_m),
		static_cast<float>(_mag_land_hold_e_m),
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

	PX4_WARN("\n[Mag-Land] Controlled landing started | hold N=%.1f E=%.1f | lat=%.7f lon=%.7f\n",
		 _mag_land_hold_n_m,
		 _mag_land_hold_e_m,
		 land_lat,
		 land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void MagnetometerMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm)
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

	PX4_WARN("\n[Mag-Land] %s disarm command sent after ground contact confirmation.\n",
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO MAGNETOMETRO - FIM ========