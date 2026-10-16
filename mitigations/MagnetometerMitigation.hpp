/****************************************************************************
 *
 *   UAVJamSim - Magnetometer Mitigation Module
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

#pragma once

#include <cstdint>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

class MagnetometerMitigation
{
public:
	// Reinicia todo o estado proprio (linha de base, anomalia, pouso).
	void reset();

	// Chamado de GZBridge::odometryCallback a cada amostra de VIO aceita,
	// para manter fresco o cache proprio de atitude usado em detectAndCorrect().
	void updateVioAttitude(uint64_t timestamp, float heading_rad, float roll_rad, float pitch_rad);

	// Chamado de GZBridge::magnetometerCallback apos o ataque ser aplicado.
	// Corrige report_x/report_y em-lugar (in-place) quando a anomalia esta
	// confirmada.
	void detectAndCorrect(bool mitigation_enabled, uint64_t timestamp,
			      float &report_x, float &report_y);

	// Pouso proprio de magnetometro: usa LPOS como estimativa de posicao
	// (unica fonte, mesmo padrao adotado para IMU).
	void checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
			    bool lpos_xy_valid, uint64_t lpos_timestamp,
			    double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	// Confirma contato com o solo por multiplas fontes antes de desarmar.
	void checkAutoDisarm(uint64_t timestamp,
			      bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
			      bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
			      bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
			      float lpos_ground_speed);

	bool isAnomalyActive() const { return _mag_anomaly_active; }
	bool isLandingActive() const { return _mag_landing_active; }
	bool isAutoLandSent() const { return _mag_auto_land_sent; }
	bool isAutoDisarmSent() const { return _mag_auto_disarm_sent; }

private:
	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm);

	// ======== Cache proprio de atitude da VIO ========
	// Alimentado por updateVioAttitude(), chamado a partir de
	// GZBridge::odometryCallback.
	bool     _vio_heading_valid{false};
	uint64_t _vio_heading_us{0};
	float    _vio_heading_rad{0.0f};
	float    _vio_roll_rad{0.0f};
	float    _vio_pitch_rad{0.0f};

	// ======== Linha de base adaptativa (offset de heading) ========
	bool     _mag_baseline_initialized{false};
	uint32_t _mag_baseline_warmup_count{0};
	double   _mag_heading_offset_rad{0.0};

	// ======== Estado da anomalia ========
	bool     _mag_anomaly_active{false};
	uint64_t _mag_anomaly_start_us{0};
	uint64_t _mag_recovery_start_us{0};

	// Contador de amostras de diagnostico ja logadas (limita o log a
	// MAG_MITIG_DIAG_MAX_COUNT vezes por episodio de anomalia).
	uint32_t _mag_mitig_diag_count{0};

	// ======== Estado de pouso/desarme (proprio) ========
	bool     _mag_landing_active{false};
	bool     _mag_auto_land_sent{false};
	bool     _mag_auto_disarm_sent{false};
	uint64_t _mag_auto_land_arrival_us{0};
	uint64_t _mag_auto_land_last_arrived_us{0};
	double   _mag_land_hold_n_m{0.0};
	double   _mag_land_hold_e_m{0.0};
	uint32_t _mag_sim_agl_ground_count{0};

	// ======== uORB proprios (nao compartilhados com o GZBridge) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

	// ======== Constantes de deteccao ========
	// Mesmos valores usados pelas mitigacoes de lidar front/2D (que tambem
	// consultam a atitude da VIO), duplicados aqui porque essa classe nao
	// tem acesso as constantes do GZBridge.
	static constexpr float MAG_MAX_TILT_RAD = 0.20f; // ~11.5 graus
	static constexpr uint64_t MAG_VIO_MAX_AGE_US = 100000ULL; // 100 ms

	static constexpr uint32_t MAG_BASELINE_WARMUP_SAMPLES = 60;
	static constexpr double   MAG_BASELINE_WARMUP_ALPHA = 0.25;
	static constexpr double MAG_BASELINE_ALPHA = 0.01;

	static constexpr uint32_t MAG_MITIG_DIAG_MAX_COUNT = 5;

	static constexpr double MAG_FIELD_HORIZ_MIN_G = 0.02;
	static constexpr double MAG_FIELD_HORIZ_MAX_G = 1.0;

	static constexpr double MAG_HEADING_ERROR_THRESHOLD_RAD = 0.35; // ~20 graus

	static constexpr uint64_t MAG_ANOMALY_CONFIRM_US = 3000000ULL;       // 3 s
	static constexpr uint64_t MAG_ANOMALY_CONFIRM_SEVERE_US = 20000ULL;  // 20 ms
	static constexpr uint64_t MAG_RECOVERY_CONFIRM_US = 500000ULL;       // 500 ms

	// ======== Constantes de pouso/desarme ========
	// Mesmos valores usados pelo pouso generico que o GZBridge ainda usa
	// para lidar/barometro.
	static constexpr float AUTO_LAND_ANOMALY_DEST_RADIUS_M = 1.2f;
	static constexpr float AUTO_LAND_ANOMALY_MAX_SPEED_M_S = 0.45f;
	static constexpr uint64_t AUTO_LAND_STABLE_US = 1500000ULL;
	static constexpr uint64_t AUTO_LAND_GROUND_SENSOR_TIMEOUT_US = 1000000ULL;
	static constexpr float AUTO_LAND_GROUND_DISARM_DIST_M = 0.18f;
	static constexpr double AUTO_LAND_SIM_GROUND_DISARM_AGL_M = 0.12;
	static constexpr float AUTO_LAND_MAX_PITCH_RAD = 0.0872665f;
	static constexpr float AUTO_LAND_MAX_ROLL_RAD = 0.0872665f;
	static constexpr uint32_t SIM_AGL_GROUND_CONFIRM_COUNT = 5;
};