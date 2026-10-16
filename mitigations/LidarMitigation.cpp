/****************************************************************************
 *
 *   UAVJamSim - LiDAR Mitigation Module (implementation)
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
 ****************************************************************************/

#include "LidarMitigation.hpp"

#include <cmath>
#include <cstring>

#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>

void LidarMitigation::reset()
{
	_lidar_baseline_initialized = false;
	_lidar_baseline_warmup_count = 0;
	_lidar_anchor_offset_m = 0.0;
	_lidar_anomaly_active = false;
	_lidar_anomaly_start_us = 0;
	_lidar_recovery_start_us = 0;
	_lidar_mitig_diag_count = 0;
	_lidar_landing_active = false;
	_lidar_auto_land_sent = false;
	_lidar_auto_disarm_sent = false;
	_lidar_auto_land_arrival_us = 0;
	_lidar_auto_land_last_arrived_us = 0;
	_lidar_land_hold_n_m = 0.0;
	_lidar_land_hold_e_m = 0.0;
	_lidar_sim_agl_ground_count = 0;

	_lidar_front_anomaly_active = false;
	_lidar_front_anomaly_start_us = 0;
	_lidar_front_recovery_start_us = 0;
	_lidar_front_mitig_diag_count = 0;
	_lidar_front_landing_active = false;
	_lidar_front_auto_land_sent = false;
	_lidar_front_auto_disarm_sent = false;
	_lidar_front_auto_land_arrival_us = 0;
	_lidar_front_auto_land_last_arrived_us = 0;
	_lidar_front_land_hold_n_m = 0.0;
	_lidar_front_land_hold_e_m = 0.0;
	_lidar_front_sim_agl_ground_count = 0;

	_lidar2d_anomaly_active = false;
	_lidar2d_anomaly_start_us = 0;
	_lidar2d_recovery_start_us = 0;
	_lidar2d_mitig_diag_count = 0;
	_lidar2d_landing_active = false;
	_lidar2d_auto_land_sent = false;
	_lidar2d_auto_disarm_sent = false;
	_lidar2d_auto_land_arrival_us = 0;
	_lidar2d_auto_land_last_arrived_us = 0;
	_lidar2d_land_hold_n_m = 0.0;
	_lidar2d_land_hold_e_m = 0.0;
	_lidar2d_sim_agl_ground_count = 0;

	// O cache da camera de profundidade (_depth_cam_*) e o cache de
	// atitude da VIO (_vio_*) nao sao resetados aqui: nenhum dos dois
	// depende do estado da mitigacao, so refletem a leitura mais recente
	// do sensor, igual outros caches de referencia do projeto.
}

void LidarMitigation::updateVioAttitude(uint64_t timestamp, float heading_rad, float roll_rad, float pitch_rad)
{
	_vio_heading_rad = heading_rad;
	_vio_roll_rad = roll_rad;
	_vio_pitch_rad = pitch_rad;
	_vio_heading_valid = true;
	_vio_heading_us = timestamp;
}

bool LidarMitigation::init(const std::string &depth_camera_topic)
{
	if (!_node.Subscribe(depth_camera_topic, &LidarMitigation::depthCameraCallback, this)) {
		PX4_WARN("failed to subscribe to %s", depth_camera_topic.c_str());
		return false;
	}

	return true;
}

// Media de um patch (janela quadrada) de pixels de profundidade em torno de
// (cx, cy). Descarta pixels invalidos (nao finitos ou <= 0) e amostras fora
// dos limites da imagem. Retorna NAN se nenhum pixel valido for encontrado
// no patch.
float LidarMitigation::samplePatchDepth(const gz::msgs::Image &msg, int cx, int cy, int patch_half)
{
	const int width = static_cast<int>(msg.width());
	const int height = static_cast<int>(msg.height());

	if (width <= 0 || height <= 0) {
		return NAN;
	}

	const char *data = msg.data().data();
	const size_t data_size = msg.data().size();

	double sum = 0.0;
	int count = 0;

	for (int dy = -patch_half; dy <= patch_half; dy++) {
		const int y = cy + dy;

		if (y < 0 || y >= height) {
			continue;
		}

		for (int dx = -patch_half; dx <= patch_half; dx++) {
			const int x = cx + dx;

			if (x < 0 || x >= width) {
				continue;
			}

			const size_t offset = (static_cast<size_t>(y) * width + x) * sizeof(float);

			if (offset + sizeof(float) > data_size) {
				continue;
			}

			float depth = 0.0f;
			memcpy(&depth, data + offset, sizeof(float));

			if (PX4_ISFINITE(depth) && depth > 0.0f) {
				sum += static_cast<double>(depth);
				count++;
			}
		}
	}

	return (count > 0) ? static_cast<float>(sum / count) : NAN;
}

// Processa a mensagem bruta da camera de profundidade: leitura central
// (usada pela mitigacao do LiDAR frontal) e leitura por setor (usada pela
// mitigacao do LiDAR 2D), projetando cada angulo de setor coberto na
// coluna de pixel correspondente, usando o campo de visao horizontal
// conhecido da camera (StereoOV7251, ver OakD-Lite/model.sdf).
void LidarMitigation::depthCameraCallback(const gz::msgs::Image &msg)
{
	const uint64_t timestamp = hrt_absolute_time();

	const int width = static_cast<int>(msg.width());
	const int height = static_cast<int>(msg.height());

	if (width <= 0 || height <= 0) {
		return;
	}

	const int patch_half = 3; // janela ~7x7 pixels
	const int cx = width / 2;
	const int cy = height / 2;

	const float center_depth = samplePatchDepth(msg, cx, cy, patch_half);

	if (PX4_ISFINITE(center_depth)) {
		_depth_cam_distance_m = center_depth;
		_depth_cam_distance_valid = true;
		_depth_cam_distance_timestamp = timestamp;
	}

	static constexpr double CAMERA_HFOV_RAD = 1.274;
	static constexpr double LIDAR2D_SECTOR_DEG = 5.0;

	const double focal_px = (static_cast<double>(width) / 2.0) / tan(CAMERA_HFOV_RAD / 2.0);
	bool any_sector_valid = false;

	for (int k = -LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k <= LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k++) {
		const double angle_rad = static_cast<double>(k) * LIDAR2D_SECTOR_DEG * M_PI / 180.0;
		const int px = cx + static_cast<int>(lround(focal_px * tan(angle_rad)));
		const int idx = k + LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS;

		const float sector_depth = samplePatchDepth(msg, px, cy, patch_half);
		_depth_cam_sector_distance_m[idx] = sector_depth;

		if (PX4_ISFINITE(sector_depth)) {
			any_sector_valid = true;
		}
	}

	if (any_sector_valid) {
		_depth_cam_sector_valid = true;
		_depth_cam_sector_timestamp = timestamp;
	}
}

// ======== MITIGACAO LIDAR - INICIO ========
// Deteccao de anomalia no sinal publicado do sensor, sem consultar a opcao
// de ataque:
//  (a) leitura fora da faixa fisica funcional do sensor;
//  (b) leitura divergindo do valor esperado por uma ancora entre a leitura
//      do LiDAR e a altitude do barometro (referencia independente - este
//      airframe nao tem VIO confiavel), calibrada a partir das primeiras
//      amostras saudaveis. O ataque e um offset constante, que uma
//      comparacao por taxa de variacao nunca pegaria (o offset se cancela
//      na diferenca entre amostras): so uma comparacao por valor absoluto
//      contra essa ancora detecta.
// Diferente das demais mitigacoes, nao exige que o veiculo ja tenha
// armado/voado: a leitura do LiDAR e significativa mesmo parado no chao
// (mede a folga ate o solo), e o ataque deve ser detectavel nesse estado.
void LidarMitigation::detectAndCorrectDown(bool mitigation_enabled, uint64_t timestamp,
					    bool baro_valid, uint64_t baro_timestamp, float baro_alt_m,
					    float min_distance, float max_distance, float &current_distance)
{
	if (!mitigation_enabled) {
		return;
	}

	// Nao ha checagem util de limite fisico aqui: current_distance ja
	// passou por math::constrain(min, max) antes desta chamada, entao uma
	// leitura saturada em min/max e absolutamente normal (nada no alcance,
	// ou muito perto) e indistinguivel de uma leitura atacada empurrada
	// para fora da faixa - as duas terminam no mesmo valor limitado. So a
	// comparacao com a ancora abaixo consegue distinguir.
	const bool non_finite = !PX4_ISFINITE(current_distance);

	const bool baro_alt_recent =
		baro_valid &&
		(timestamp >= baro_timestamp) &&
		((timestamp - baro_timestamp) <= LIDAR_BARO_MAX_AGE_US) &&
		PX4_ISFINITE(baro_alt_m);

	double residual = 0.0;
	bool residual_anomaly = false;

	if (baro_alt_recent && _lidar_baseline_initialized) {
		const double expected_dist = _lidar_anchor_offset_m + static_cast<double>(baro_alt_m);
		residual = static_cast<double>(current_distance) - expected_dist;
		residual_anomaly = fabs(residual) > LIDAR_RESIDUAL_THRESHOLD_M;
	}

	const bool sample_anomalous = non_finite || residual_anomaly;
	const bool sample_severe = non_finite;

	if (!sample_anomalous) {

		// Atualiza a ancora apenas com amostras saudaveis. As primeiras
		// amostras (warmup) calibram com um alpha mais agressivo; depois,
		// adapta lentamente (tolera deriva legitima de
		// calibracao/temperatura, mas um ataque sustentado nao chega a
		// ser absorvido, pois so entra aqui quando a amostra nao e
		// anomala).
		if (baro_alt_recent) {
			const double sample_offset = static_cast<double>(current_distance) - static_cast<double>(baro_alt_m);

			if (_lidar_baseline_warmup_count == 0) {
				_lidar_anchor_offset_m = sample_offset;
				_lidar_baseline_warmup_count = 1;

			} else if (_lidar_baseline_warmup_count < LIDAR_BASELINE_WARMUP_SAMPLES) {
				_lidar_anchor_offset_m += LIDAR_BASELINE_WARMUP_ALPHA * (sample_offset - _lidar_anchor_offset_m);
				_lidar_baseline_warmup_count++;

				if (_lidar_baseline_warmup_count >= LIDAR_BASELINE_WARMUP_SAMPLES) {
					_lidar_baseline_initialized = true;
				}

			} else {
				_lidar_anchor_offset_m += LIDAR_BASELINE_ALPHA * (sample_offset - _lidar_anchor_offset_m);
			}
		}

		if (_lidar_anomaly_active) {
			if (_lidar_recovery_start_us == 0) {
				_lidar_recovery_start_us = timestamp;

			} else if ((timestamp - _lidar_recovery_start_us) >= LIDAR_RECOVERY_CONFIRM_US) {
				_lidar_anomaly_active = false;
				_lidar_anomaly_start_us = 0;
				_lidar_mitig_diag_count = 0;

				PX4_INFO("\n[Lidar-Mitig] Sensor anomaly cleared\n");
			}
		}

	} else {
		_lidar_recovery_start_us = 0;

		if (_lidar_anomaly_start_us == 0) {
			_lidar_anomaly_start_us = timestamp;
		}

		const uint64_t confirm_required_us =
			sample_severe ? LIDAR_ANOMALY_CONFIRM_SEVERE_US : LIDAR_ANOMALY_CONFIRM_US;

		if (!_lidar_anomaly_active &&
		    ((timestamp - _lidar_anomaly_start_us) >= confirm_required_us)) {
			_lidar_anomaly_active = true;

			PX4_WARN("\n[Lidar-Mitig] Sensor anomaly detected | dist=%.2f m | residual=%.2f m | finite=%s\n",
				 static_cast<double>(current_distance),
				 residual,
				 non_finite ? "no" : "yes");
		}

		// Reconstroi a leitura a partir da ancora e da altitude atual do
		// barometro, preservando a fisica do sensor (nunca fora dos
		// limites). So corrige quando o barometro e a ancora ja
		// calibrada estao disponiveis; sem isso, deixa a leitura crua
		// passar (melhor que travar em um valor desatualizado).
		if (_lidar_anomaly_active && baro_alt_recent && _lidar_baseline_initialized) {
			const double expected_dist = _lidar_anchor_offset_m + static_cast<double>(baro_alt_m);
			current_distance = static_cast<float>(math::constrain(
						    expected_dist,
						    static_cast<double>(min_distance),
						    static_cast<double>(max_distance)));
		}

		if (_lidar_anomaly_active && (_lidar_mitig_diag_count < LIDAR_MITIG_DIAG_MAX_COUNT)) {
			static uint64_t lidar_mitig_diag_last_us = 0;

			if ((timestamp - lidar_mitig_diag_last_us) > 1000000ULL) {
				lidar_mitig_diag_last_us = timestamp;
				_lidar_mitig_diag_count++;

				PX4_INFO("\n[Lidar-Mitig] Status | dist=%.2f m | residual=%.2f m | baro_alt=%s\n",
					 static_cast<double>(current_distance),
					 residual,
					 baro_alt_recent ? "ok" : "stale");

				if (_lidar_mitig_diag_count == LIDAR_MITIG_DIAG_MAX_COUNT) {
					PX4_WARN("\n[Lidar-Mitig] Check: listener distance_sensor (down, inst 0)\n");
				}
			}
		}
	}
}
// ======== MITIGACAO LIDAR - FIM ========

void LidarMitigation::checkAutoLandDown(uint64_t timestamp, MapProjection &pos_ref,
					 bool lpos_xy_valid, uint64_t lpos_timestamp,
					 double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	checkAutoLandShared(timestamp, pos_ref, _lidar_anomaly_active,
			     _lidar_landing_active, _lidar_auto_land_sent,
			     _lidar_auto_land_arrival_us, _lidar_auto_land_last_arrived_us,
			     _lidar_land_hold_n_m, _lidar_land_hold_e_m,
			     lpos_xy_valid, lpos_timestamp, lpos_n_m, lpos_e_m, lpos_ground_speed,
			     "Lidar");
}

void LidarMitigation::checkAutoDisarmDown(uint64_t timestamp,
					   bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					   bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					   bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					   float lpos_ground_speed)
{
	checkAutoDisarmShared(timestamp,
			       _lidar_landing_active, _lidar_auto_land_sent, _lidar_auto_disarm_sent,
			       _lidar_land_hold_n_m, _lidar_land_hold_e_m,
			       _lidar_sim_agl_ground_count,
			       ground_distance_valid, ground_distance_timestamp, ground_distance_m,
			       sim_agl_valid, sim_agl_timestamp, sim_agl_m,
			       sim_att_valid, sim_att_timestamp, sim_roll_rad, sim_pitch_rad,
			       lpos_ground_speed,
			       "Lidar");
}

// ======== MITIGACAO LIDAR FRONTAL - INICIO ========
// Deteccao de anomalia comparando a leitura publicada contra a camera de
// profundidade (OakD-Lite, so presente no x500_uavjamsim), referencia
// independente do LiDAR. Diferente da mitigacao do LiDAR para baixo, os
// dois sensores medem a mesma coisa em tempo real (nao e um offset
// constante que se cancela na diferenca entre amostras aqui, ja que a
// camera nao esta sob o mesmo ataque): e uma comparacao direta, sem
// ancora/calibracao. Sem essa camera disponivel (qualquer outro
// airframe), a deteccao simplesmente nao roda - nao ha limite fisico util
// aqui pelo mesmo motivo do LiDAR para baixo (saturacao no maximo e
// indistinguivel de ataque empurrando para fora da faixa).
void LidarMitigation::detectAndCorrectFront(bool mitigation_enabled, uint64_t timestamp,
					     float min_distance, float max_distance, float &current_distance)
{
	if (!mitigation_enabled) {
		return;
	}

	const bool vio_att_recent =
		_vio_heading_valid &&
		(timestamp >= _vio_heading_us) &&
		((timestamp - _vio_heading_us) <= MAG_VIO_MAX_AGE_US);

	const bool vio_near_level =
		vio_att_recent &&
		(fabsf(_vio_roll_rad) < LIDAR_FRONT_MAX_TILT_RAD) &&
		(fabsf(_vio_pitch_rad) < LIDAR_FRONT_MAX_TILT_RAD);

	const bool depth_cam_recent =
		vio_near_level &&
		_depth_cam_distance_valid &&
		(timestamp >= _depth_cam_distance_timestamp) &&
		((timestamp - _depth_cam_distance_timestamp) <= LIDAR_FRONT_DEPTH_MAX_AGE_US) &&
		PX4_ISFINITE(_depth_cam_distance_m);

	double residual = 0.0;
	bool sample_anomalous = false;

	if (depth_cam_recent) {
		residual = static_cast<double>(current_distance) - static_cast<double>(_depth_cam_distance_m);
		sample_anomalous = fabs(residual) > LIDAR_FRONT_RESIDUAL_THRESHOLD_M;
	}

	if (!sample_anomalous) {

		if (_lidar_front_anomaly_active) {
			if (_lidar_front_recovery_start_us == 0) {
				_lidar_front_recovery_start_us = timestamp;

			} else if ((timestamp - _lidar_front_recovery_start_us) >= LIDAR_FRONT_RECOVERY_CONFIRM_US) {
				_lidar_front_anomaly_active = false;
				_lidar_front_anomaly_start_us = 0;
				_lidar_front_mitig_diag_count = 0;

				PX4_INFO("\n[LidarFront-Mitig] Sensor anomaly cleared\n");
			}
		}

	} else {
		_lidar_front_recovery_start_us = 0;

		if (_lidar_front_anomaly_start_us == 0) {
			_lidar_front_anomaly_start_us = timestamp;
		}

		if (!_lidar_front_anomaly_active &&
		    ((timestamp - _lidar_front_anomaly_start_us) >= LIDAR_FRONT_ANOMALY_CONFIRM_US)) {
			_lidar_front_anomaly_active = true;

			PX4_WARN("\n[LidarFront-Mitig] Sensor anomaly detected | dist=%.2f m | depth_cam=%.2f m | residual=%.2f m\n",
				 static_cast<double>(current_distance),
				 static_cast<double>(_depth_cam_distance_m),
				 residual);
		}

		// Substitui pela leitura da camera de profundidade, preservando
		// os limites fisicos do sensor. So corrige quando a camera esta
		// disponivel e recente; sem isso, deixa a leitura crua passar.
		if (_lidar_front_anomaly_active && depth_cam_recent) {
			current_distance = static_cast<float>(math::constrain(
						    static_cast<double>(_depth_cam_distance_m),
						    static_cast<double>(min_distance),
						    static_cast<double>(max_distance)));
		}

		if (_lidar_front_anomaly_active && (_lidar_front_mitig_diag_count < LIDAR_FRONT_MITIG_DIAG_MAX_COUNT)) {
			static uint64_t lidar_front_mitig_diag_last_us = 0;

			if ((timestamp - lidar_front_mitig_diag_last_us) > 1000000ULL) {
				lidar_front_mitig_diag_last_us = timestamp;
				_lidar_front_mitig_diag_count++;

				PX4_INFO("\n[LidarFront-Mitig] Status | dist=%.2f m | residual=%.2f m | depth_cam=%s\n",
					 static_cast<double>(current_distance),
					 residual,
					 depth_cam_recent ? "ok" : "stale");

				if (_lidar_front_mitig_diag_count == LIDAR_FRONT_MITIG_DIAG_MAX_COUNT) {
					PX4_WARN("\n[LidarFront-Mitig] Check: listener distance_sensor (front, inst 1)\n");
				}
			}
		}
	}
}
// ======== MITIGACAO LIDAR FRONTAL - FIM ========

void LidarMitigation::checkAutoLandFront(uint64_t timestamp, MapProjection &pos_ref,
					  bool lpos_xy_valid, uint64_t lpos_timestamp,
					  double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	checkAutoLandShared(timestamp, pos_ref, _lidar_front_anomaly_active,
			     _lidar_front_landing_active, _lidar_front_auto_land_sent,
			     _lidar_front_auto_land_arrival_us, _lidar_front_auto_land_last_arrived_us,
			     _lidar_front_land_hold_n_m, _lidar_front_land_hold_e_m,
			     lpos_xy_valid, lpos_timestamp, lpos_n_m, lpos_e_m, lpos_ground_speed,
			     "LidarFront");
}

void LidarMitigation::checkAutoDisarmFront(uint64_t timestamp,
					    bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					    bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					    bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					    float lpos_ground_speed)
{
	checkAutoDisarmShared(timestamp,
			       _lidar_front_landing_active, _lidar_front_auto_land_sent, _lidar_front_auto_disarm_sent,
			       _lidar_front_land_hold_n_m, _lidar_front_land_hold_e_m,
			       _lidar_front_sim_agl_ground_count,
			       ground_distance_valid, ground_distance_timestamp, ground_distance_m,
			       sim_agl_valid, sim_agl_timestamp, sim_agl_m,
			       sim_att_valid, sim_att_timestamp, sim_roll_rad, sim_pitch_rad,
			       lpos_ground_speed,
			       "LidarFront");
}

// ======== MITIGACAO LIDAR 2D - INICIO ========
// Compara os setores dentro do alcance da camera de profundidade
// (OakD-Lite, so presente no x500_uavjamsim) contra a leitura publicada,
// usando o residuo MEDIO entre os setores comparaveis (nao um unico
// setor) - o ataque aplica o mesmo offset constante a todo o scan de uma
// vez, entao concordancia entre varios setores da mais confianca que um
// so. Fora da faixa coberta pela camera nao ha nenhuma referencia
// independente disponivel nesse modelo: esses setores permanecem sem
// protecao - limitacao fisica real, nao lacuna de implementacao (mesma
// razao ja documentada na mitigacao do LiDAR frontal).
void LidarMitigation::detectAndCorrectTwoD(bool mitigation_enabled, uint64_t timestamp,
					    float angle_offset, float increment,
					    uint16_t max_distance_cm, uint16_t min_distance_cm,
					    uint16_t *distances, int distances_len)
{
	if (!mitigation_enabled) {
		return;
	}

	const bool vio_att_recent =
		_vio_heading_valid &&
		(timestamp >= _vio_heading_us) &&
		((timestamp - _vio_heading_us) <= MAG_VIO_MAX_AGE_US);

	const bool vio_near_level =
		vio_att_recent &&
		(fabsf(_vio_roll_rad) < LIDAR2D_MAX_TILT_RAD) &&
		(fabsf(_vio_pitch_rad) < LIDAR2D_MAX_TILT_RAD);

	const bool depth_sectors_recent =
		vio_near_level &&
		_depth_cam_sector_valid &&
		(timestamp >= _depth_cam_sector_timestamp) &&
		((timestamp - _depth_cam_sector_timestamp) <= LIDAR2D_DEPTH_MAX_AGE_US);

	const int front_index = static_cast<int>(lroundf(-angle_offset / increment));

	double residual_sum = 0.0;
	int residual_count = 0;

	if (depth_sectors_recent) {
		for (int k = -LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k <= LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k++) {
			const int idx = front_index + k;

			if (idx < 0 || idx >= distances_len) {
				continue;
			}

			const float cam_depth = _depth_cam_sector_distance_m[k + LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS];

			if (!PX4_ISFINITE(cam_depth)) {
				continue;
			}

			// So compara setores com leitura real do lidar (pula o
			// sentinela de "nada detectado"; "muito perto" (0) entra
			// na comparacao normalmente, ja que carrega informacao real).
			if (distances[idx] > max_distance_cm) {
				continue;
			}

			const double lidar_m = static_cast<double>(distances[idx]) / 100.0;
			const double residual = lidar_m - static_cast<double>(cam_depth);

			residual_sum += fabs(residual);
			residual_count++;
		}
	}

	const bool enough_data = residual_count >= LIDAR2D_MIN_VALID_SECTORS;
	const bool sample_anomalous = enough_data &&
				       ((residual_sum / residual_count) > LIDAR2D_RESIDUAL_THRESHOLD_M);

	if (!sample_anomalous) {

		if (_lidar2d_anomaly_active) {
			if (_lidar2d_recovery_start_us == 0) {
				_lidar2d_recovery_start_us = timestamp;

			} else if ((timestamp - _lidar2d_recovery_start_us) >= LIDAR2D_RECOVERY_CONFIRM_US) {
				_lidar2d_anomaly_active = false;
				_lidar2d_anomaly_start_us = 0;
				_lidar2d_mitig_diag_count = 0;

				PX4_INFO("\n[Lidar2D-Mitig] Sensor anomaly cleared\n");
			}
		}

	} else {
		_lidar2d_recovery_start_us = 0;

		if (_lidar2d_anomaly_start_us == 0) {
			_lidar2d_anomaly_start_us = timestamp;
		}

		if (!_lidar2d_anomaly_active &&
		    ((timestamp - _lidar2d_anomaly_start_us) >= LIDAR2D_ANOMALY_CONFIRM_US)) {
			_lidar2d_anomaly_active = true;

			PX4_WARN("\n[Lidar2D-Mitig] Sensor anomaly detected | residual_avg=%.2f m | setores=%d\n",
				 residual_sum / residual_count, residual_count);
		}

		// Substitui cada setor coberto pela leitura da camera, respeitando
		// os limites do sensor. So corrige os setores onde a camera tem
		// leitura valida; os demais (mesmo dentro da faixa coberta)
		// ficam com a leitura crua.
		if (_lidar2d_anomaly_active && depth_sectors_recent) {
			for (int k = -LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k <= LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS; k++) {
				const int idx = front_index + k;

				if (idx < 0 || idx >= distances_len) {
					continue;
				}

				const float cam_depth = _depth_cam_sector_distance_m[k + LIDAR2D_DEPTH_COVERAGE_HALF_SECTORS];

				if (!PX4_ISFINITE(cam_depth)) {
					continue;
				}

				const double cam_cm = static_cast<double>(cam_depth) * 100.0;

				if (cam_cm >= static_cast<double>(max_distance_cm)) {
					distances[idx] = max_distance_cm + 1;

				} else if (cam_cm < static_cast<double>(min_distance_cm)) {
					distances[idx] = 0;

				} else {
					distances[idx] = static_cast<uint16_t>(cam_cm);
				}
			}
		}

		if (_lidar2d_anomaly_active && (_lidar2d_mitig_diag_count < LIDAR2D_MITIG_DIAG_MAX_COUNT)) {
			static uint64_t lidar2d_mitig_diag_last_us = 0;

			if ((timestamp - lidar2d_mitig_diag_last_us) > 1000000ULL) {
				lidar2d_mitig_diag_last_us = timestamp;
				_lidar2d_mitig_diag_count++;

				PX4_INFO("\n[Lidar2D-Mitig] Status | residual_avg=%.2f m | setores=%d | depth_cam=%s\n",
					 enough_data ? (residual_sum / residual_count) : -1.0,
					 residual_count,
					 depth_sectors_recent ? "ok" : "stale");

				if (_lidar2d_mitig_diag_count == LIDAR2D_MITIG_DIAG_MAX_COUNT) {
					PX4_WARN("\n[Lidar2D-Mitig] Check: listener obstacle_distance (front sectors)\n");
				}
			}
		}
	}
}
// ======== MITIGACAO LIDAR 2D - FIM ========

void LidarMitigation::checkAutoLandTwoD(uint64_t timestamp, MapProjection &pos_ref,
					 bool lpos_xy_valid, uint64_t lpos_timestamp,
					 double lpos_n_m, double lpos_e_m, float lpos_ground_speed)
{
	checkAutoLandShared(timestamp, pos_ref, _lidar2d_anomaly_active,
			     _lidar2d_landing_active, _lidar2d_auto_land_sent,
			     _lidar2d_auto_land_arrival_us, _lidar2d_auto_land_last_arrived_us,
			     _lidar2d_land_hold_n_m, _lidar2d_land_hold_e_m,
			     lpos_xy_valid, lpos_timestamp, lpos_n_m, lpos_e_m, lpos_ground_speed,
			     "Lidar2D");
}

void LidarMitigation::checkAutoDisarmTwoD(uint64_t timestamp,
					   bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					   bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					   bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					   float lpos_ground_speed)
{
	checkAutoDisarmShared(timestamp,
			       _lidar2d_landing_active, _lidar2d_auto_land_sent, _lidar2d_auto_disarm_sent,
			       _lidar2d_land_hold_n_m, _lidar2d_land_hold_e_m,
			       _lidar2d_sim_agl_ground_count,
			       ground_distance_valid, ground_distance_timestamp, ground_distance_m,
			       sim_agl_valid, sim_agl_timestamp, sim_agl_m,
			       sim_att_valid, sim_att_timestamp, sim_roll_rad, sim_pitch_rad,
			       lpos_ground_speed,
			       "Lidar2D");
}

// ======== MITIGACAO DE POUSO LIDAR (DOWN/FRONT/2D) - INICIO ========
// Helper privado, reaproveitado pelos tres sensores: a logica de
// chegada/confirmacao de solo e identica entre eles, so o estado (qual
// sensor) muda a cada chamada.
void LidarMitigation::checkAutoLandShared(uint64_t timestamp, MapProjection &pos_ref,
					   bool anomaly_active,
					   bool &landing_active, bool &auto_land_sent,
					   uint64_t &auto_land_arrival_us, uint64_t &auto_land_last_arrived_us,
					   double &land_hold_n_m, double &land_hold_e_m,
					   bool lpos_xy_valid, uint64_t lpos_timestamp,
					   double lpos_n_m, double lpos_e_m, float lpos_ground_speed,
					   const char *log_tag)
{
	if (!anomaly_active || auto_land_sent || !pos_ref.isInitialized()) {
		auto_land_arrival_us = 0;
		auto_land_last_arrived_us = 0;
		return;
	}

	if (!lpos_xy_valid || (timestamp - lpos_timestamp) > 500000ULL) {
		auto_land_arrival_us = 0;
		auto_land_last_arrived_us = 0;
		return;
	}

	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		auto_land_arrival_us = 0;
		auto_land_last_arrived_us = 0;
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
		auto_land_arrival_us = 0;
		auto_land_last_arrived_us = 0;
		return;
	}

	auto_land_last_arrived_us = timestamp;

	if (auto_land_arrival_us == 0) {
		auto_land_arrival_us = timestamp;

		land_hold_n_m = lpos_n_m;
		land_hold_e_m = lpos_e_m;

		PX4_INFO("\n[%s-Land] Destination reached | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 log_tag,
			 static_cast<double>(horizontal_error),
			 static_cast<double>(lpos_ground_speed),
			 static_cast<double>(AUTO_LAND_STABLE_US) / 1e6);

		return;
	}

	const uint64_t preland_elapsed_us = timestamp - auto_land_arrival_us;

	if (preland_elapsed_us < AUTO_LAND_STABLE_US) {
		return;
	}

	landing_active = true;
	auto_land_sent = true;

	publishLandCommand(timestamp, pos_ref, land_hold_n_m, land_hold_e_m, log_tag);
}

// Confirma contato com o solo por multiplas fontes (land detector do PX4,
// sensor de distancia, altura simulada) antes de desarmar. A anomalia
// original nunca e usada como criterio aqui.
void LidarMitigation::checkAutoDisarmShared(uint64_t timestamp,
					     bool &landing_active, bool &auto_land_sent, bool &auto_disarm_sent,
					     double land_hold_n_m, double land_hold_e_m,
					     uint32_t &sim_agl_ground_count,
					     bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
					     bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
					     bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
					     float lpos_ground_speed,
					     const char *log_tag)
{
	(void)land_hold_n_m;
	(void)land_hold_e_m;

	if (!landing_active || !auto_land_sent || auto_disarm_sent) {
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
			sim_agl_ground_count++;
		} else {
			sim_agl_ground_count = 0;
		}
	} else {
		sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

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
		PX4_WARN("\n[%s-Land] PX4 landing detector confirmed landed - normal disarm\n", log_tag);

	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[%s-Land] PX4 landing detector reported ground contact - safety disarm\n", log_tag);

	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[%s-Land] Downward distance sensor reported ground contact | dist=%.2f m - safety disarm\n",
			 log_tag,
			 static_cast<double>(ground_distance_m));

	} else {
		PX4_WARN("\n[%s-Land] Simulated ground proximity reached | agl=%.2f m - safety disarm\n",
			 log_tag,
			 static_cast<double>(sim_agl_m));
	}

	publishDisarmCommand(timestamp, force_disarm, log_tag);
	auto_disarm_sent = true;
	auto_land_sent = true;
	landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void LidarMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref,
					  double land_hold_n_m, double land_hold_e_m, const char *log_tag)
{
	double land_lat = 0.0;
	double land_lon = 0.0;
	pos_ref.reproject(
		static_cast<float>(land_hold_n_m),
		static_cast<float>(land_hold_e_m),
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

	PX4_WARN("\n[%s-Land] Controlled landing started | hold N=%.1f E=%.1f | lat=%.7f lon=%.7f\n",
		 log_tag,
		 land_hold_n_m,
		 land_hold_e_m,
		 land_lat,
		 land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void LidarMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm, const char *log_tag)
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

	PX4_WARN("\n[%s-Land] %s disarm command sent after ground contact confirmation.\n",
		 log_tag,
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO LIDAR (DOWN/FRONT/2D) - FIM ========