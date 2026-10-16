/****************************************************************************
 *
 *   UAVJamSim - IMU Mitigation Module (implementation)
 *    Detecta anomalias no acelerometro/giroscopio combinando tres sinais:
 *     1) Limites fisicos de forca especifica (fora de 0.2g-4g e implausivel).
 *     2) Z-score contra uma linha de base adaptativa (media/variancia).
 *     3) Divergencia entre a aceleracao horizontal medida pela IMU e a
 *        aceleracao horizontal estimada por diferenca finita da velocidade
 *        de GPS (referencia cruzada independente).
 *
 *   Quando confirmada, a amostra e substituida por um valor reconstruido a
 *   partir da odometria visual (VIO) - accel/gyro no referencial do corpo,
 *   com ruido gaussiano pequeno somado para nao parecer um valor "limpo
 *   demais" para o EKF do PX4.
 *
 ****************************************************************************/

#include "ImuMitigation.hpp"

#include <cmath>
#include <cstdlib>

#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>

void ImuMitigation::reset()
{
	_imu_baseline_initialized = false;
	_imu_accel_norm_ewma = 9.80665;
	_imu_accel_norm_var_ewma = 1.0;
	_imu_gyro_norm_ewma = 0.0;
	_imu_gyro_norm_var_ewma = 0.02;

	_imu_anomaly_active = false;
	_imu_anomaly_start_us = 0;
	_imu_recovery_start_us = 0;

	_imu_ever_armed = false;
	_imu_prev_armed = false;
	_imu_land_settle_until_us = 0;

	_vio_imu_accel_valid = false;
	_vio_imu_accel_us = 0;
	_vio_imu_accel_body[0] = 0.0f;
	_vio_imu_accel_body[1] = 0.0f;
	_vio_imu_accel_body[2] = -9.80665f;

	_vio_imu_gyro_valid = false;
	_vio_imu_gyro_us = 0;
	_vio_imu_gyro_body[0] = 0.0f;
	_vio_imu_gyro_body[1] = 0.0f;
	_vio_imu_gyro_body[2] = 0.0f;

	_imu_ref_gps_prev_valid = false;
	_imu_ref_gps_prev_us = 0;
	_imu_ref_gps_prev_vel_n = 0.0f;
	_imu_ref_gps_prev_vel_e = 0.0f;

	_imu_ref_gps_accel_valid = false;
	_imu_ref_gps_accel_us = 0;
	_imu_ref_gps_accel_n = 0.0f;
	_imu_ref_gps_accel_e = 0.0f;

	_imu_landing_active = false;
	_imu_auto_land_sent = false;
	_imu_auto_disarm_sent = false;
	_imu_auto_land_arrival_us = 0;
	_imu_auto_land_last_arrived_us = 0;
	_imu_land_hold_n_m = 0.0;
	_imu_land_hold_e_m = 0.0;
	_imu_sim_agl_ground_count = 0;
}

float ImuMitigation::generateWgn()
{
	// Implementacao Box-Muller polar com reaproveitamento alternado das amostras.
	static float V1, V2, S;
	static bool phase = true;
	float X;

	if (phase) {
		do {
			float U1 = (float)rand() / (float)RAND_MAX;
			float U2 = (float)rand() / (float)RAND_MAX;
			V1 = 2.0f * U1 - 1.0f;
			V2 = 2.0f * U2 - 1.0f;
			S = V1 * V1 + V2 * V2;
		} while (S >= 1.0f || fabsf(S) < 1e-8f);

		X = V1 * float(sqrtf(-2.0f * float(logf(S)) / S));

	} else {
		X = V2 * float(sqrtf(-2.0f * float(logf(S)) / S));
	}

	phase = !phase;
	return X;
}

void ImuMitigation::updateVioAccelSubstitute(uint64_t timestamp, float accel_x, float accel_y, float accel_z)
{
	_vio_imu_accel_body[0] = accel_x;
	_vio_imu_accel_body[1] = accel_y;
	_vio_imu_accel_body[2] = accel_z;
	_vio_imu_accel_valid = true;
	_vio_imu_accel_us = timestamp;
}

void ImuMitigation::updateVioGyroSubstitute(uint64_t timestamp, float gyro_x, float gyro_y, float gyro_z)
{
	_vio_imu_gyro_body[0] = gyro_x;
	_vio_imu_gyro_body[1] = gyro_y;
	_vio_imu_gyro_body[2] = gyro_z;
	_vio_imu_gyro_valid = true;
	_vio_imu_gyro_us = timestamp;
}

// ======== MITIGACAO IMU - INICIO ========
// Aceleracao horizontal por diferenca finita da velocidade GPS, usada como
// referencia cruzada independente do acelerometro.
void ImuMitigation::updateGpsAccelReference(uint64_t timestamp, float vel_north, float vel_east)
{
	if (_imu_ref_gps_prev_valid && (timestamp > _imu_ref_gps_prev_us)) {
		const double dt_ref = static_cast<double>(timestamp - _imu_ref_gps_prev_us) * 1e-6;

		if (dt_ref >= IMU_REF_GPS_MIN_DT_S && dt_ref <= IMU_REF_GPS_MAX_DT_S) {
			const double accel_n_est =
				(static_cast<double>(vel_north) - static_cast<double>(_imu_ref_gps_prev_vel_n)) / dt_ref;
			const double accel_e_est =
				(static_cast<double>(vel_east) - static_cast<double>(_imu_ref_gps_prev_vel_e)) / dt_ref;

			if (PX4_ISFINITE(accel_n_est) && PX4_ISFINITE(accel_e_est)) {
				_imu_ref_gps_accel_n = static_cast<float>(accel_n_est);
				_imu_ref_gps_accel_e = static_cast<float>(accel_e_est);
				_imu_ref_gps_accel_valid = true;
				_imu_ref_gps_accel_us = timestamp;
			}
		}
	}

	_imu_ref_gps_prev_vel_n = vel_north;
	_imu_ref_gps_prev_vel_e = vel_east;
	_imu_ref_gps_prev_us = timestamp;
	_imu_ref_gps_prev_valid = true;
}
// ======== MITIGACAO IMU - FIM ========

// ======== MITIGACAO IMU - INICIO ========
// Deteccao de anomalia no sinal publicado do sensor (accel/gyro ja com o
// ataque aplicado, se houver). Combina tres verificacoes:
//  (1) Limites fisicos de forca especifica.
//  (2) Z-score contra uma linha de base (media do acelerometro fixa na
//      gravidade local; media/variancia do giroscopio adaptativas).
//  (3) Aceleracao horizontal medida pela IMU vs. aceleracao horizontal
//      estimada por diferenca finita da velocidade GPS.
bool ImuMitigation::detectAndSubstitute(bool mitigation_enabled, uint64_t timestamp,
					 bool vehicle_armed, bool vehicle_not_landed,
					 float &accel_x, float &accel_y, float &accel_z,
					 float &gyro_x, float &gyro_y, float &gyro_z)
{
	// A deteccao so roda depois que o veiculo esteve armado e voando (nao
	// pousado) pela primeira vez desde que a mitigacao foi habilitada.
	if (vehicle_armed && vehicle_not_landed) {
		_imu_ever_armed = true;
	}

	// Suspende a deteccao por uma janela curta na transicao de armado para
	// desarmado: o corte do empuxo pode gerar uma leitura transitoria de
	// forca especifica proxima de zero, fisicamente identica a uma IMU
	// zerada, sem relacao com ataque.
	if (!vehicle_armed && _imu_prev_armed) {
		_imu_land_settle_until_us = timestamp + IMU_LAND_SETTLE_US;
	}

	_imu_prev_armed = vehicle_armed;

	if (!(mitigation_enabled && _imu_ever_armed && (timestamp >= _imu_land_settle_until_us))) {
		return _imu_anomaly_active;
	}

	const double accel_x_d = static_cast<double>(accel_x);
	const double accel_y_d = static_cast<double>(accel_y);
	const double accel_z_d = static_cast<double>(accel_z);
	const double gyro_x_d  = static_cast<double>(gyro_x);
	const double gyro_y_d  = static_cast<double>(gyro_y);
	const double gyro_z_d  = static_cast<double>(gyro_z);

	const double accel_norm = sqrt(
					      accel_x_d * accel_x_d +
					      accel_y_d * accel_y_d +
					      accel_z_d * accel_z_d);

	const double gyro_norm = sqrt(
					     gyro_x_d * gyro_x_d +
					     gyro_y_d * gyro_y_d +
					     gyro_z_d * gyro_z_d);

	// (1) Limites fisicos de forca especifica.
	const bool accel_out_of_bounds =
		!PX4_ISFINITE(accel_norm) ||
		(accel_norm < IMU_ACCEL_NORM_MIN_MS2) ||
		(accel_norm > IMU_ACCEL_NORM_MAX_MS2);

	// (2) Z-score contra a linha de base.
	const double accel_std = sqrt(math::max(_imu_accel_norm_var_ewma, 1e-6));
	const double accel_zscore = fabs(accel_norm - _imu_accel_norm_ewma) / accel_std;

	double gyro_zscore = 0.0;

	if (_imu_baseline_initialized) {
		const double gyro_std = sqrt(math::max(_imu_gyro_norm_var_ewma, 1e-6));
		gyro_zscore = fabs(gyro_norm - _imu_gyro_norm_ewma) / gyro_std;
	}

	const bool statistical_anomaly =
		(accel_zscore > IMU_ZSCORE_THRESHOLD) ||
		(_imu_baseline_initialized && (gyro_zscore > IMU_ZSCORE_THRESHOLD));

	// (3) Aceleracao horizontal da IMU vs. aceleracao horizontal do GPS.
	// So se aplica quando a referencia de GPS esta recente.
	const bool gps_ref_recent =
		_imu_ref_gps_accel_valid &&
		(timestamp >= _imu_ref_gps_accel_us) &&
		((timestamp - _imu_ref_gps_accel_us) <= IMU_REF_GPS_MAX_AGE_US);

	double gps_crosscheck_residual = 0.0;
	bool gps_crosscheck_anomaly = false;

	if (gps_ref_recent) {
		const double accel_horiz = sqrt(accel_x_d * accel_x_d + accel_y_d * accel_y_d);

		const double gps_accel_n_d = static_cast<double>(_imu_ref_gps_accel_n);
		const double gps_accel_e_d = static_cast<double>(_imu_ref_gps_accel_e);
		const double gps_horiz = sqrt(gps_accel_n_d * gps_accel_n_d + gps_accel_e_d * gps_accel_e_d);

		gps_crosscheck_residual = fabs(accel_horiz - gps_horiz);
		gps_crosscheck_anomaly = gps_crosscheck_residual > IMU_CROSSCHECK_RESIDUAL_MAX_MS2;
	}

	const bool sample_anomalous =
		accel_out_of_bounds || statistical_anomaly || gps_crosscheck_anomaly;

	// Anomalia severa: violacao fisica dura (limites/GPS) ou z-score acima
	// de IMU_ZSCORE_SEVERE_THRESHOLD. Usa a janela de confirmacao reduzida.
	const bool sample_severe =
		accel_out_of_bounds ||
		gps_crosscheck_anomaly ||
		(accel_zscore > IMU_ZSCORE_SEVERE_THRESHOLD) ||
		(_imu_baseline_initialized && (gyro_zscore > IMU_ZSCORE_SEVERE_THRESHOLD));

	// Log periodico de diagnostico, apenas enquanto a anomalia esta ativa.
	if (_imu_anomaly_active) {
		static uint64_t imu_mitig_diag_last_us = 0;

		if ((timestamp - imu_mitig_diag_last_us) > 1000000ULL) {
			imu_mitig_diag_last_us = timestamp;

			PX4_INFO("\n[IMU-Mitig] Status | accel_norm=%.2f (z=%.1f) gyro_norm=%.3f (z=%.1f) | "
				 "gps_ref=%s residual=%.2f\n",
				 accel_norm, accel_zscore, gyro_norm, gyro_zscore,
				 gps_ref_recent ? "ok" : "stale",
				 gps_crosscheck_residual);
		}
	}

	if (!sample_anomalous) {

		// Atualiza a linha de base com a amostra saudavel. A media do
		// acelerometro permanece fixa; variancia do acelerometro e
		// media/variancia do giroscopio se adaptam.
		if (!_imu_baseline_initialized) {
			_imu_gyro_norm_ewma  = gyro_norm;
			_imu_accel_norm_var_ewma = 1.0;
			_imu_gyro_norm_var_ewma  = 0.1;
			_imu_baseline_initialized = true;

		} else {
			const double d_accel = accel_norm - _imu_accel_norm_ewma;
			const double d_gyro  = gyro_norm - _imu_gyro_norm_ewma;

			_imu_gyro_norm_ewma += IMU_BASELINE_ALPHA * d_gyro;

			_imu_accel_norm_var_ewma =
				(1.0 - IMU_BASELINE_ALPHA) * _imu_accel_norm_var_ewma +
				IMU_BASELINE_ALPHA * d_accel * d_accel;

			_imu_gyro_norm_var_ewma =
				(1.0 - IMU_BASELINE_ALPHA) * _imu_gyro_norm_var_ewma +
				IMU_BASELINE_ALPHA * d_gyro * d_gyro;

			_imu_accel_norm_var_ewma = math::constrain(_imu_accel_norm_var_ewma, 0.05, 4.0);
			_imu_gyro_norm_var_ewma  = math::constrain(_imu_gyro_norm_var_ewma, 0.02, 2.0);
		}

		if (_imu_anomaly_active) {
			// Confirma recuperacao apos um intervalo estavel sem anomalia.
			if (_imu_recovery_start_us == 0) {
				_imu_recovery_start_us = timestamp;

			} else if ((timestamp - _imu_recovery_start_us) >= IMU_RECOVERY_CONFIRM_US) {
				_imu_anomaly_active = false;
				_imu_anomaly_start_us = 0;

				PX4_INFO("\n[IMU-Mitig] Sensor anomaly cleared | accel_norm=%.2f gyro_norm=%.3f\n",
					 accel_norm, gyro_norm);
			}
		}

	} else {

		_imu_recovery_start_us = 0;

		if (_imu_anomaly_start_us == 0) {
			_imu_anomaly_start_us = timestamp;
		}

		const uint64_t confirm_required_us =
			sample_severe ? IMU_ANOMALY_CONFIRM_SEVERE_US : IMU_ANOMALY_CONFIRM_US;

		if (!_imu_anomaly_active &&
		    ((timestamp - _imu_anomaly_start_us) >= confirm_required_us)) {
			_imu_anomaly_active = true;

			PX4_WARN("\n[IMU-Mitig] Sensor anomaly detected | accel_norm=%.2f (z=%.1f) gyro_norm=%.3f (z=%.1f) | "
				 "bounds=%s gps_residual=%.2f (%s) | severe=%s confirm=%.0fms\n",
				 accel_norm, accel_zscore, gyro_norm, gyro_zscore,
				 accel_out_of_bounds ? "out" : "ok",
				 gps_crosscheck_residual,
				 gps_crosscheck_anomaly ? "anomalous" : (gps_ref_recent ? "ok" : "stale"),
				 sample_severe ? "yes" : "no",
				 static_cast<double>(confirm_required_us) / 1000.0);
		}

		// Substitui accel/gyro pelo substituto dinamico reconstruido a
		// partir da VIO (ver updateVioAccelSubstitute/updateVioGyroSubstitute,
		// chamados de GZBridge::odometryCallback). Uma amostra severa
		// (violacao fisica dura ou z-score muito acima do normal) e
		// substituida imediatamente, mesmo antes da anomalia ser
		// oficialmente confirmada: isso evita que qualquer amostra
		// obviamente atacada chegue crua ao EKF do PX4 durante a janela
		// de confirmacao. A confirmacao oficial (log/pouso) continua
		// exigindo a janela de debounce normalmente.
		if (_imu_anomaly_active || sample_severe) {
			const bool vio_accel_recent =
				_vio_imu_accel_valid &&
				(timestamp >= _vio_imu_accel_us) &&
				((timestamp - _vio_imu_accel_us) <= IMU_VIO_SUBSTITUTE_MAX_AGE_US);

			const bool vio_gyro_recent =
				_vio_imu_gyro_valid &&
				(timestamp >= _vio_imu_gyro_us) &&
				((timestamp - _vio_imu_gyro_us) <= IMU_VIO_SUBSTITUTE_MAX_AGE_US);

			if (vio_accel_recent) {
				accel_x = _vio_imu_accel_body[0] + generateWgn() * IMU_VIO_SUBSTITUTE_ACCEL_NOISE_STD;
				accel_y = _vio_imu_accel_body[1] + generateWgn() * IMU_VIO_SUBSTITUTE_ACCEL_NOISE_STD;
				accel_z = _vio_imu_accel_body[2] + generateWgn() * IMU_VIO_SUBSTITUTE_ACCEL_NOISE_STD;
			}

			if (vio_gyro_recent) {
				gyro_x = _vio_imu_gyro_body[0] + generateWgn() * IMU_VIO_SUBSTITUTE_GYRO_NOISE_STD;
				gyro_y = _vio_imu_gyro_body[1] + generateWgn() * IMU_VIO_SUBSTITUTE_GYRO_NOISE_STD;
				gyro_z = _vio_imu_gyro_body[2] + generateWgn() * IMU_VIO_SUBSTITUTE_GYRO_NOISE_STD;
			}

			static uint64_t vio_substitute_log_last_us = 0;

			if ((timestamp - vio_substitute_log_last_us) > 1000000ULL) {
				vio_substitute_log_last_us = timestamp;

				PX4_INFO("\n[IMU-Mitig] VIO substitute | accel=%s gyro=%s\n",
					 vio_accel_recent ? "active" : "stale",
					 vio_gyro_recent ? "active" : "stale");
			}
		}
	}

	return _imu_anomaly_active;
}
// ======== MITIGACAO IMU - FIM ========

// ======== MITIGACAO DE POUSO IMU - INICIO ========
// Pouso proprio de IMU: diferente de GPS (que tem blackout e ruido como
// origens distintas), IMU tem uma unica fonte de anomalia e usa LPOS como
// unica estimativa de posicao confiavel - nao precisa de pseudo-medicao
// propria como o GPS.
void ImuMitigation::checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
				   bool lpos_xy_valid, uint64_t lpos_timestamp,
				   double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	if (!_imu_anomaly_active || _imu_auto_land_sent || !pos_ref.isInitialized()) {
		_imu_auto_land_arrival_us = 0;
		_imu_auto_land_last_arrived_us = 0;
		return;
	}

	if (!lpos_xy_valid || (timestamp - lpos_timestamp) > 500000ULL) {
		_imu_auto_land_arrival_us = 0;
		_imu_auto_land_last_arrived_us = 0;
		return;
	}

	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		_imu_auto_land_arrival_us = 0;
		_imu_auto_land_last_arrived_us = 0;
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
		_imu_auto_land_arrival_us = 0;
		_imu_auto_land_last_arrived_us = 0;
		return;
	}

	_imu_auto_land_last_arrived_us = timestamp;

	if (_imu_auto_land_arrival_us == 0) {
		_imu_auto_land_arrival_us = timestamp;

		_imu_land_hold_n_m = lpos_n_m;
		_imu_land_hold_e_m = lpos_e_m;

		PX4_INFO("\n[IMU-Land] Destination reached | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 static_cast<double>(horizontal_error),
			 static_cast<double>(lpos_ground_speed),
			 static_cast<double>(AUTO_LAND_STABLE_US) / 1e6);

		return;
	}

	const uint64_t preland_elapsed_us = timestamp - _imu_auto_land_arrival_us;

	if (preland_elapsed_us < AUTO_LAND_STABLE_US) {
		return;
	}

	_imu_landing_active = true;
	_imu_auto_land_sent = true;

	publishLandCommand(timestamp, pos_ref);
}

// Confirma contato com o solo por multiplas fontes (land detector do PX4,
// sensor de distancia, altura simulada) antes de desarmar. A anomalia
// original nunca e usada como criterio aqui.
void ImuMitigation::checkAutoDisarm(uint64_t timestamp,
				     bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				     bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				     bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				     float lpos_ground_speed)
{
	if (!_imu_landing_active || !_imu_auto_land_sent || _imu_auto_disarm_sent) {
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

	// Ground-truth do Gazebo, usado exclusivamente para confirmar contato com
	// o solo durante o pouso, nunca para detectar a anomalia original.
	const bool sim_ground_recent =
		sim_agl_valid &&
		(timestamp >= sim_agl_timestamp) &&
		((timestamp - sim_agl_timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US) &&
		PX4_ISFINITE(sim_agl_m);

	if (sim_ground_recent) {
		if (sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) {
			_imu_sim_agl_ground_count++;
		} else {
			_imu_sim_agl_ground_count = 0;
		}
	} else {
		_imu_sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(_imu_sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

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
		PX4_WARN("\n[IMU-Land] PX4 landing detector confirmed landed - normal disarm\n");

	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[IMU-Land] PX4 landing detector reported ground contact - safety disarm\n");

	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[IMU-Land] Downward distance sensor reported ground contact | dist=%.2f m - safety disarm\n",
			 static_cast<double>(ground_distance_m));

	} else {
		PX4_WARN("\n[IMU-Land] Simulated ground proximity reached | agl=%.2f m - safety disarm\n",
			 static_cast<double>(sim_agl_m));
	}

	publishDisarmCommand(timestamp, force_disarm);
	_imu_auto_disarm_sent = true;
	_imu_auto_land_sent = true;
	_imu_landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void ImuMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref)
{
	double land_lat = 0.0;
	double land_lon = 0.0;
	pos_ref.reproject(
		static_cast<float>(_imu_land_hold_n_m),
		static_cast<float>(_imu_land_hold_e_m),
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

	PX4_WARN("\n[IMU-Land] Controlled landing started | hold N=%.1f E=%.1f | lat=%.7f lon=%.7f\n",
		 _imu_land_hold_n_m,
		 _imu_land_hold_e_m,
		 land_lat,
		 land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void ImuMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm)
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

	PX4_WARN("\n[IMU-Land] %s disarm command sent after ground contact confirmation.\n",
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO IMU - FIM ========