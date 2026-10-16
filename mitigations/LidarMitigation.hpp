/****************************************************************************
 *
 *   UAVJamSim - LiDAR Mitigation Module (down / front / 2D)
 *
 *   Um unico arquivo cobre os tres sensores LiDAR, porque compartilham a
 *   mesma tecnica de ataque (offset constante, via LidarAttack) e tecnicas
 *   de deteccao com o mesmo formato geral (ancora/referencia independente +
 *   janela de confirmacao), mas cada um usa uma referencia diferente:
 *
 *     - Down (terrain-following): ancora contra a altitude do barometro
 *       (nao ha VIO confiavel nesse airframe). Como o ataque e um offset
 *       constante, so uma comparacao por VALOR ABSOLUTO contra a ancora
 *       detecta - uma comparacao por taxa de variacao nunca pegaria (o
 *       offset se cancela na diferenca entre amostras).
 *     - Front (obstaculo a frente): comparacao direta contra a camera de
 *       profundidade (OakD-Lite, so presente no x500_uavjamsim) - os dois
 *       sensores medem a mesma coisa em tempo real, sem precisar de
 *       ancora/calibracao.
 *     - 2D (scan de obstaculos): mesma ideia do front, mas comparando cada
 *       setor do scan coberto pelo campo de visao da camera, usando o
 *       residuo MEDIO entre os setores comparaveis (o ataque aplica o
 *       mesmo offset a todo o scan de uma vez, entao concordancia entre
 *       varios setores da mais confianca que um so).
 *
 *   Possui seu proprio no gz-transport e assina o topico de imagem da
 *   camera de profundidade diretamente, processando a mensagem bruta
 *   (amostragem de patch central e por setor). Mantem um cache proprio de
 *   atitude da VIO (heading/roll/pitch), atualizado a partir de
 *   GZBridge::odometryCallback.
 *
 *   O pouso/desarme de cada um dos tres sensores usa um helper privado
 *   interno em comum, ja que a logica de chegada/confirmacao de solo e
 *   identica entre eles -
 *   so o estado (qual sensor) muda a cada chamada.
 *
 ****************************************************************************/

#pragma once

#include <cstdint>
#include <string>

#include <gz/msgs.hh>
#include <gz/transport.hh>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

class LidarMitigation
{
public:
	explicit LidarMitigation(gz::transport::Node &node) : _node(node) {}

	void reset();

	// Assina o topico da camera de profundidade (OakD-Lite, so presente no
	// x500_uavjamsim). Mesmo nome fixo/limitacao ja documentados no
	// GZBridge original: nao escopado por mundo ou instancia de modelo, so
	// funciona corretamente com um unico veiculo rodando por vez.
	bool init(const std::string &depth_camera_topic = "/depth_camera");

	// ======== LiDAR down (terrain-following) ========
	// Chamado de GZBridge apos o ataque ser aplicado ao sensor down.
	// baro_alt_m/baro_valid/baro_timestamp sao a referencia independente
	// (cache de barometro, compartilhado com outras mitigacoes).
	void detectAndCorrectDown(bool mitigation_enabled, uint64_t timestamp,
				   bool baro_valid, uint64_t baro_timestamp, float baro_alt_m,
				   float min_distance, float max_distance, float &current_distance);

	void checkAutoLandDown(uint64_t timestamp, MapProjection &pos_ref,
				bool lpos_xy_valid, uint64_t lpos_timestamp,
				double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	void checkAutoDisarmDown(uint64_t timestamp,
				  bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				  bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				  bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				  float lpos_ground_speed);

	// ======== Cache de atitude da VIO ========
	// Chamado de GZBridge::odometryCallback a cada amostra de VIO aceita.
	void updateVioAttitude(uint64_t timestamp, float heading_rad, float roll_rad, float pitch_rad);

	// ======== LiDAR front (obstaculo a frente) ========
	void detectAndCorrectFront(bool mitigation_enabled, uint64_t timestamp,
				    float min_distance, float max_distance, float &current_distance);

	void checkAutoLandFront(uint64_t timestamp, MapProjection &pos_ref,
				 bool lpos_xy_valid, uint64_t lpos_timestamp,
				 double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	void checkAutoDisarmFront(uint64_t timestamp,
				   bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				   bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				   bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				   float lpos_ground_speed);

	// ======== LiDAR 2D (scan de obstaculos) ========
	// distances/distances_len e o array bruto (em cm) da mensagem
	// obstacle_distance, corrigido em-lugar (in-place) por setor.
	void detectAndCorrectTwoD(bool mitigation_enabled, uint64_t timestamp,
				   float angle_offset, float increment,
				   uint16_t max_distance_cm, uint16_t min_distance_cm,
				   uint16_t *distances, int distances_len);

	void checkAutoLandTwoD(uint64_t timestamp, MapProjection &pos_ref,
				bool lpos_xy_valid, uint64_t lpos_timestamp,
				double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	void checkAutoDisarmTwoD(uint64_t timestamp,
				  bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				  bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				  bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				  float lpos_ground_speed);

	bool isDownAnomalyActive() const { return _lidar_anomaly_active; }
	bool isFrontAnomalyActive() const { return _lidar_front_anomaly_active; }
	bool isTwoDAnomalyActive() const { return _lidar2d_anomaly_active; }

private:
	// Processa a mensagem bruta da camera de profundidade: leitura central
	// (LiDAR front) e por setor (LiDAR 2D), alimentando o cache proprio
	// diretamente (sem depender do GZBridge para nada alem de repassar a
	// mensagem, ja que so esta classe consome esses dados).
	void depthCameraCallback(const gz::msgs::Image &msg);
	float samplePatchDepth(const gz::msgs::Image &msg, int cx, int cy, int patch_half);

	// Helpers privados compartilhados pelos tres sensores - a logica de
	// chegada/confirmacao de solo e identica entre eles, so o estado
	// (referencias passadas) muda a cada chamada.
	void checkAutoLandShared(uint64_t timestamp, MapProjection &pos_ref,
				  bool anomaly_active,
				  bool &landing_active, bool &auto_land_sent,
				  uint64_t &auto_land_arrival_us, uint64_t &auto_land_last_arrived_us,
				  double &land_hold_n_m, double &land_hold_e_m,
				  bool lpos_xy_valid, uint64_t lpos_timestamp,
				  double lpos_n_m, double lpos_e_m, float lpos_ground_speed,
				  const char *log_tag);

	void checkAutoDisarmShared(uint64_t timestamp,
				    bool &landing_active, bool &auto_land_sent, bool &auto_disarm_sent,
				    double land_hold_n_m, double land_hold_e_m,
				    uint32_t &sim_agl_ground_count,
				    bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
				    bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
				    bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
				    float lpos_ground_speed,
				    const char *log_tag);

	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref,
				 double land_hold_n_m, double land_hold_e_m, const char *log_tag);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm, const char *log_tag);

	gz::transport::Node &_node;

	// ======== Cache proprio de atitude da VIO ========
	bool     _vio_heading_valid{false};
	uint64_t _vio_heading_us{0};
	float    _vio_heading_rad{0.0f};
	float    _vio_roll_rad{0.0f};
	float    _vio_pitch_rad{0.0f};

	// ======== Cache de camera de profundidade (exclusivo desta classe) ========
	bool     _depth_cam_distance_valid{false};
	uint64_t _depth_cam_distance_timestamp{0};
	float    _depth_cam_distance_m{NAN};

	static constexpr int LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS = 7; // +-7 setores (~+-35 graus)
	static constexpr int LIDAR2D_DEPTH_COVERAGE_COUNT = 2 * LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS + 1;

	float    _depth_cam_sector_distance_m[LIDAR2D_DEPTH_COVERAGE_COUNT] = {};
	bool     _depth_cam_sector_valid{false};
	uint64_t _depth_cam_sector_timestamp{0};

	// ======== Estado LiDAR down ========
	bool     _lidar_baseline_initialized{false};
	uint32_t _lidar_baseline_warmup_count{0};
	double   _lidar_anchor_offset_m{0.0}; // distancia_lida - altitude_baro, na calibracao

	bool     _lidar_anomaly_active{false};
	uint64_t _lidar_anomaly_start_us{0};
	uint64_t _lidar_recovery_start_us{0};
	uint32_t _lidar_mitig_diag_count{0};

	bool     _lidar_landing_active{false};
	bool     _lidar_auto_land_sent{false};
	bool     _lidar_auto_disarm_sent{false};
	uint64_t _lidar_auto_land_arrival_us{0};
	uint64_t _lidar_auto_land_last_arrived_us{0};
	double   _lidar_land_hold_n_m{0.0};
	double   _lidar_land_hold_e_m{0.0};
	uint32_t _lidar_sim_agl_ground_count{0};

	// ======== Estado LiDAR front ========
	bool     _lidar_front_anomaly_active{false};
	uint64_t _lidar_front_anomaly_start_us{0};
	uint64_t _lidar_front_recovery_start_us{0};
	uint32_t _lidar_front_mitig_diag_count{0};

	bool     _lidar_front_landing_active{false};
	bool     _lidar_front_auto_land_sent{false};
	bool     _lidar_front_auto_disarm_sent{false};
	uint64_t _lidar_front_auto_land_arrival_us{0};
	uint64_t _lidar_front_auto_land_last_arrived_us{0};
	double   _lidar_front_land_hold_n_m{0.0};
	double   _lidar_front_land_hold_e_m{0.0};
	uint32_t _lidar_front_sim_agl_ground_count{0};

	// ======== Estado LiDAR 2D ========
	bool     _lidar2d_anomaly_active{false};
	uint64_t _lidar2d_anomaly_start_us{0};
	uint64_t _lidar2d_recovery_start_us{0};
	uint32_t _lidar2d_mitig_diag_count{0};

	bool     _lidar2d_landing_active{false};
	bool     _lidar2d_auto_land_sent{false};
	bool     _lidar2d_auto_disarm_sent{false};
	uint64_t _lidar2d_auto_land_arrival_us{0};
	uint64_t _lidar2d_auto_land_last_arrived_us{0};
	double   _lidar2d_land_hold_n_m{0.0};
	double   _lidar2d_land_hold_e_m{0.0};
	uint32_t _lidar2d_sim_agl_ground_count{0};

	// ======== uORB proprios (um por sensor, para pouso independente) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

	// ======== Constantes - LiDAR down ========
	static constexpr uint32_t LIDAR_BASELINE_WARMUP_SAMPLES = 30;
	static constexpr double   LIDAR_BASELINE_WARMUP_ALPHA = 0.25;
	static constexpr double   LIDAR_BASELINE_ALPHA = 0.01;
	static constexpr uint32_t LIDAR_MITIG_DIAG_MAX_COUNT = 3;
	static constexpr double LIDAR_RESIDUAL_THRESHOLD_M = 1.2;
	static constexpr uint64_t LIDAR_ANOMALY_CONFIRM_US = 1500000ULL;        // 1.5 s
	static constexpr uint64_t LIDAR_ANOMALY_CONFIRM_SEVERE_US = 20000ULL;   // 20 ms
	static constexpr uint64_t LIDAR_RECOVERY_CONFIRM_US = 500000ULL;        // 500 ms
	static constexpr uint64_t LIDAR_BARO_MAX_AGE_US = 800000ULL;            // 800 ms

	// ======== Constantes - LiDAR front ========
	static constexpr uint64_t LIDAR_FRONT_DEPTH_MAX_AGE_US = 300000ULL; // 300 ms
	static constexpr uint32_t LIDAR_FRONT_MITIG_DIAG_MAX_COUNT = 3;
	static constexpr double LIDAR_FRONT_RESIDUAL_THRESHOLD_M = 1.0;
	static constexpr float LIDAR_FRONT_MAX_TILT_RAD = 0.05f; // ~3 graus
	static constexpr uint64_t LIDAR_FRONT_ANOMALY_CONFIRM_US = 500000ULL;  // 500 ms
	static constexpr uint64_t LIDAR_FRONT_RECOVERY_CONFIRM_US = 500000ULL; // 500 ms

	// ======== Constantes - LiDAR 2D ========
	static constexpr uint64_t LIDAR2D_DEPTH_MAX_AGE_US = 300000ULL; // 300 ms
	static constexpr uint32_t LIDAR2D_MITIG_DIAG_MAX_COUNT = 3;
	static constexpr double LIDAR2D_RESIDUAL_THRESHOLD_M = 1.5;
	static constexpr float LIDAR2D_MAX_TILT_RAD = 0.05f; // ~3 graus
	static constexpr int    LIDAR2D_MIN_VALID_SECTORS = 3;
	static constexpr uint64_t LIDAR2D_ANOMALY_CONFIRM_US = 500000ULL;  // 500 ms
	static constexpr uint64_t LIDAR2D_RECOVERY_CONFIRM_US = 500000ULL; // 500 ms

	// Duplicada aqui: usada tambem por magnetometro/lidar front/2D para
	// validar a idade da atitude da VIO (cache que continua no GZBridge).
	static constexpr uint64_t MAG_VIO_MAX_AGE_US = 100000ULL; // 100 ms

	// ======== Constantes de pouso/desarme (mesmos valores do molde antigo) ========
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