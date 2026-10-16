/****************************************************************************
 *
 *   UAVJamSim - IMU Mitigation Module
 *
 *   Detecta anomalias no acelerometro/giroscopio combinando tres sinais:
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

#pragma once

#include <cstdint>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

class ImuMitigation
{
public:
	// Reinicia todo o estado proprio (linha de base, anomalia, caches de
	// substituicao). Chamado quando a mitigacao geral e ligada/desligada.
	void reset();

	// Chamado de GZBridge::imuCallback a cada amostra de IMU. Detecta
	// anomalia e, se confirmada (ou severa), substitui accel/gyro pelo
	// valor reconstruido via VIO, em-lugar (in-place). vehicle_armed e
	// vehicle_not_landed vêm de fora porque usam as mesmas subscricoes
	// uORB compartilhadas com outras mitigacoes. Retorna true se a
	// anomalia esta confirmada neste ciclo (usado tambem por
	// checkAutoLand internamente, via isAnomalyActive()).
	bool detectAndSubstitute(bool mitigation_enabled, uint64_t timestamp,
				  bool vehicle_armed, bool vehicle_not_landed,
				  float &accel_x, float &accel_y, float &accel_z,
				  float &gyro_x, float &gyro_y, float &gyro_z);

	// Chamado de GZBridge::odometryCallback quando uma amostra de VIO e
	// aceita, para manter fresco o cache de substituicao usado por
	// detectAndSubstitute() durante uma anomalia.
	void updateVioAccelSubstitute(uint64_t timestamp, float accel_x, float accel_y, float accel_z);
	void updateVioGyroSubstitute(uint64_t timestamp, float gyro_x, float gyro_y, float gyro_z);

	// Chamado de GZBridge::navSatCallback a cada amostra de GPS nominal
	// (fora de ancora/blackout/jamming pulsado), para manter a referencia
	// cruzada de aceleracao horizontal usada na deteccao (3).
	void updateGpsAccelReference(uint64_t timestamp, float vel_north, float vel_east);

	// Chamado de GZBridge::checkImuAutoLand. Pouso proprio de IMU: usa LPOS
	// como estimativa de posicao (unica fonte, diferente de GPS que tem
	// blackout e ruido) e verifica chegada ao destino da missao.
	void checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
			    bool lpos_xy_valid, uint64_t lpos_timestamp,
			    double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	// Chamado de GZBridge::checkImuAutoDisarm. Confirma contato com o solo
	// por multiplas fontes (land detector do PX4, sensor de distancia,
	// altura simulada) antes de desarmar.
	void checkAutoDisarm(uint64_t timestamp,
			      bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
			      bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
			      bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
			      float lpos_ground_speed);

	bool isAnomalyActive() const { return _imu_anomaly_active; }
	bool isLandingActive() const { return _imu_landing_active; }
	bool isAutoLandSent() const { return _imu_auto_land_sent; }
	bool isAutoDisarmSent() const { return _imu_auto_disarm_sent; }

private:
	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm);

	// Gera uma amostra de ruido branco gaussiano com media zero e desvio
	// padrao unitario. Implementacao propria, identica (mesmo algoritmo
	// Box-Muller polar) a GZBridge::generate_wgn().
	static float generateWgn();

	// ======== Linha de base adaptativa ========
	bool   _imu_baseline_initialized{false};
	double _imu_accel_norm_ewma{9.80665};
	double _imu_accel_norm_var_ewma{1.0};
	double _imu_gyro_norm_ewma{0.0};
	double _imu_gyro_norm_var_ewma{0.02};

	// ======== Estado da anomalia ========
	bool     _imu_anomaly_active{false};
	uint64_t _imu_anomaly_start_us{0};
	uint64_t _imu_recovery_start_us{0};

	// ======== Controle de armado/pouso (janela de suspensao pos-desarme) ========
	bool     _imu_ever_armed{false};
	bool     _imu_prev_armed{false};
	uint64_t _imu_land_settle_until_us{0};

	// ======== Cache de substituicao reconstruido via VIO ========
	bool     _vio_imu_accel_valid{false};
	uint64_t _vio_imu_accel_us{0};
	float    _vio_imu_accel_body[3]{0.0f, 0.0f, -9.80665f};

	bool     _vio_imu_gyro_valid{false};
	uint64_t _vio_imu_gyro_us{0};
	float    _vio_imu_gyro_body[3]{0.0f, 0.0f, 0.0f};

	// ======== Cache de referencia cruzada com GPS ========
	bool     _imu_ref_gps_prev_valid{false};
	uint64_t _imu_ref_gps_prev_us{0};
	float    _imu_ref_gps_prev_vel_n{0.0f};
	float    _imu_ref_gps_prev_vel_e{0.0f};

	bool     _imu_ref_gps_accel_valid{false};
	uint64_t _imu_ref_gps_accel_us{0};
	float    _imu_ref_gps_accel_n{0.0f};
	float    _imu_ref_gps_accel_e{0.0f};

	// ======== Estado de pouso/desarme ========
	bool     _imu_landing_active{false};
	bool     _imu_auto_land_sent{false};
	bool     _imu_auto_disarm_sent{false};
	uint64_t _imu_auto_land_arrival_us{0};
	uint64_t _imu_auto_land_last_arrived_us{0};
	double   _imu_land_hold_n_m{0.0};
	double   _imu_land_hold_e_m{0.0};
	uint32_t _imu_sim_agl_ground_count{0};

	// ======== uORB proprios (nao compartilhados com o GZBridge) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

	// Constantes de pouso/desarme (mesmos valores usados pelo pouso generico
	// que o GZBridge ainda usa para motor/mag/lidar/baro).
	static constexpr float AUTO_LAND_ANOMALY_DEST_RADIUS_M = 1.2f;
	static constexpr float AUTO_LAND_ANOMALY_MAX_SPEED_M_S = 0.45f;
	static constexpr uint64_t AUTO_LAND_STABLE_US = 1500000ULL;
	static constexpr uint64_t AUTO_LAND_GROUND_SENSOR_TIMEOUT_US = 1000000ULL;
	static constexpr float AUTO_LAND_GROUND_DISARM_DIST_M = 0.18f;
	static constexpr double AUTO_LAND_SIM_GROUND_DISARM_AGL_M = 0.12;
	static constexpr float AUTO_LAND_MAX_PITCH_RAD = 0.0872665f;
	static constexpr float AUTO_LAND_MAX_ROLL_RAD = 0.0872665f;
	static constexpr uint32_t SIM_AGL_GROUND_CONFIRM_COUNT = 5;

	// ======== Constantes ========
	static constexpr uint64_t IMU_LAND_SETTLE_US = 1000000ULL; // 1 s

	static constexpr double IMU_ACCEL_NORM_MIN_MS2 = 2.0;   // ~0.2 g
	static constexpr double IMU_ACCEL_NORM_MAX_MS2 = 39.0;  // ~4 g

	static constexpr double IMU_ZSCORE_THRESHOLD = 6.0;
	static constexpr double IMU_ZSCORE_SEVERE_THRESHOLD = 12.0;
	static constexpr double IMU_BASELINE_ALPHA = 0.01;

	static constexpr uint64_t IMU_ANOMALY_CONFIRM_US = 300000ULL;        // 300 ms
	static constexpr uint64_t IMU_ANOMALY_CONFIRM_SEVERE_US = 20000ULL;  // 20 ms
	static constexpr uint64_t IMU_RECOVERY_CONFIRM_US = 500000ULL;       // 500 ms

	static constexpr uint64_t IMU_REF_GPS_MAX_AGE_US = 800000ULL; // 800 ms
	static constexpr double   IMU_REF_GPS_MIN_DT_S = 0.05;
	static constexpr double   IMU_REF_GPS_MAX_DT_S = 2.0;

	static constexpr double IMU_CROSSCHECK_RESIDUAL_MAX_MS2 = 4.0;

	static constexpr uint64_t IMU_VIO_SUBSTITUTE_MAX_AGE_US = 400000ULL; // 400 ms
	static constexpr float IMU_VIO_SUBSTITUTE_ACCEL_NOISE_STD = 0.02f;   // m/s^2
	static constexpr float IMU_VIO_SUBSTITUTE_GYRO_NOISE_STD = 0.003f;   // rad/s
};