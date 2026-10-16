/****************************************************************************
 *
 *   UAVJamSim - GPS Mitigation Module (implementation)
 *
 *  *   ESTRATEGIAS IMPLEMENTADAS
 *   =========================
 *
 *   1) Filtro de Kalman simples (posicao N/E, altitude, velocidade 3D).
 *      A cada amostra de GPS, o filtro PREDIZ onde o drone deveria estar
 *      (com base na velocidade estimada) e compara essa predicao contra a
 *      medicao recebida. A discrepancia (inovacao) e normalizada pela
 *      incerteza do proprio filtro, gerando o NIS (Normalized Innovation
 *      Squared) - um numero que decola quando a medicao e estatisticamente
 *      incompativel com o historico recente do voo. NIS alto = GPS suspeito.
 *      Isso detecta qualquer ataque que desloque a posicao/altitude/
 *      velocidade reportada, sem precisar conhecer a assinatura especifica
 *      de cada tipo de ataque (offset constante, rotacao, ruido de jamming).
 *
 *   2) Pseudo-medicao por dead-reckoning quando o GPS falha ou mente.
 *      Quando a mitigacao decide que a amostra atual nao e confiavel (seja
 *      por NIS alto, seja por blackout completo), o sistema PARA de usar o
 *      GPS e passa a estimar a posicao sozinho, priorizando fontes na
 *      seguinte ordem: (a) odometria visual (VIO), a mais confiavel por
 *      ser independente do GPS; (b) estimativa local do proprio PX4 (LPOS),
 *      que ja fundiu IMU com outras referencias; (c) ultima velocidade
 *      valida conhecida, extrapolada no tempo com decaimento (para nao
 *      "fugir" indefinidamente caso a velocidade real tenha mudado). Essa
 *      posicao estimada e o que efetivamente sai como sensor_gps publicado
 *      enquanto o ataque persiste - por isso "pseudo-medicao": do ponto de
 *      vista do resto do PX4 (EKF2, navegador), continua parecendo GPS
 *      normal, so que a fonte real da informacao mudou por baixo.
 *
 *   3) Referencia cruzada com o barometro (calibracao mutua de altitude).
 *      Toda vez que uma amostra de GPS e aceita como nominal (nao anomala),
 *      esta classe calibra um offset entre a altitude do filtro de Kalman
 *      e a altitude que o barometro esta reportando naquele instante
 *      (ctx.jmit_baro_alt_offset). O barometro usa esse offset como sua
 *      propria referencia independente para detectar ataques de offset de
 *      pressao - ou seja, GPS e barometro se vigiam mutuamente: um ataque
 *      que so afete um dos dois sensores fica evidente na discrepancia
 *      contra o outro. Essa calibracao so acontece no ramo "nominal" -
 *      nunca durante um ataque confirmado, para nao ensinar o offset errado.
 *
 *   4) Pouso e desarme automaticos como ultima linha de defesa.
 *      Se o ataque persiste por tempo suficiente (blackout sustentado, ou
 *      ruido/offset que nunca some), a mitigacao guia o drone ate a
 *      posicao-alvo da missao usando a pseudo-medicao acima, pousa no local
 *      e desarma assim que confirma contato com o solo (por multiplas
 *      fontes: land detector do PX4, sensor de distancia, altura simulada).
 *      Isso limita o dano de um ataque de GPS que nunca seria resolvido de
 *      outra forma: em vez de o drone continuar voando cego indefinidamente
 *      atras de uma posicao falsa, ele para com seguranca.
 ****************************************************************************/

#include "GpsMitigation.hpp"

#include <cmath>

#include <mathlib/mathlib.h>
#include <px4_platform_common/log.h>
#include <drivers/drv_sensor.h>
#include <lib/drivers/device/Device.hpp>

// Reinicia todo o estado interno (filtro de Kalman, blackout, pouso/desarme)
// para os valores iniciais - chamado quando a mitigacao geral e ligada ou
// desligada, para nao carregar estado de uma sessao anterior.
void GpsMitigation::reset()
{
	_kf_n_m = 0.0; _kf_e_m = 0.0; _kf_alt = 0.0;
	_kf_vel_n = 0.0f; _kf_vel_e = 0.0f; _kf_vel_d = 0.0f;
	_kf_P_n = 1.0; _kf_P_e = 1.0; _kf_P_alt = 4.0;
	_kf_P_vn = 0.1f; _kf_P_ve = 0.1f; _kf_P_vd = 0.1f;
	_kf_initialized = false;
	_kf_last_us = 0;
	_kf_attack_dur_us = 0;
	_kf_attack_first = true;

	_jmit_pub_n_m = 0.0; _jmit_pub_e_m = 0.0; _jmit_pub_alt = 0.0;
	_jmit_pub_vel_n = 0.0f; _jmit_pub_vel_e = 0.0f; _jmit_pub_vel_d = 0.0f;

	_blackout_anchor_initialized = false;
	_blackout_start_us = 0;
	_blackout_last_pub_us = 0;
	_blackout_last_dr_us = 0;
	_blackout_detected = false;

	_gps_auto_land_sent = false;
	_gps_auto_disarm_sent = false;
	_gps_landing_active = false;
	_gps_land_source_blackout = false;
	_gps_land_source_severe_anomaly = false;
	_gps_auto_land_arrival_us = 0;
	_gps_auto_land_last_arrived_us = 0;
	_gps_land_hold_start_us = 0;
	_gps_land_hold_n_m = 0.0;
	_gps_land_hold_e_m = 0.0;
	_gps_land_start_alt_msl = 0.0;
	_gps_land_target_alt_msl = 0.0;
	_gps_land_last_alt_msl = 0.0;
	_gps_land_last_alt_valid = false;
	_gps_land_last_update_us = 0;
	_gps_anomaly_severe = false;
	_gps_anomaly_severe_us = 0;
	_gps_land_heading_rad = 0.0f;
	_gps_land_heading_valid = false;

	_sim_agl_ground_count = 0;
}

// ======== MITIGACAO GPS - INICIO ========
// Detecta medicoes anomalas por NIS e publica uma pseudo-medicao degradada.
bool GpsMitigation::detectAndCorrect(bool mitigation_enabled, MapProjection &pos_ref, GpsMitigationContext &ctx,
				     double &latitude, double &longitude, double &altitude,
				     float &vel_north, float &vel_east, float &vel_down)
{
	const uint64_t now = hrt_absolute_time();
	bool gps_anomalous_now = false;

	// Projecao da medicao GPS atual para coordenadas locais em metros.
	float cur_n_m, cur_e_m;
	pos_ref.project(latitude, longitude, cur_n_m, cur_e_m);
	const double meas_n   = static_cast<double>(cur_n_m);
	const double meas_e   = static_cast<double>(cur_e_m);
	const double meas_alt = altitude;

	const bool lpos_xy_recent =
		ctx.lpos_xy_valid &&
		(now >= ctx.lpos_timestamp) &&
		((now - ctx.lpos_timestamp) <= 500000ULL);

	const bool lpos_alt_recent =
		ctx.jmit_lpos_alt_valid &&
		(now >= ctx.jmit_lpos_timestamp) &&
		((now - ctx.jmit_lpos_timestamp) <= 500000ULL) &&
		PX4_ISFINITE(ctx.jmit_lpos_alt_msl);

	const bool baro_alt_recent =
		ctx.jmit_baro_valid &&
		ctx.jmit_baro_alt_offset_valid &&
		(now >= ctx.jmit_baro_timestamp) &&
		((now - ctx.jmit_baro_timestamp) <= 500000ULL) &&
		PX4_ISFINITE(ctx.jmit_baro_alt_m);

	const double trusted_n = lpos_xy_recent ? ctx.lpos_n_m : meas_n;
	const double trusted_e = lpos_xy_recent ? ctx.lpos_e_m : meas_e;
	// Altitude "de confianca" para a inicializacao: prioriza LPOS (ja
	// fundido pelo proprio PX4), depois barometro corrigido pelo ultimo
	// offset calibrado, e por ultimo a propria medicao de GPS - a mesma
	// logica de fallback usada no dead-reckoning de blackout.
	const double trusted_alt = lpos_alt_recent ?
		ctx.jmit_lpos_alt_msl :
		(baro_alt_recent ?
			static_cast<double>(ctx.jmit_baro_alt_m) + ctx.jmit_baro_alt_offset :
			meas_alt);

	// Inicializacao do filtro com a primeira estimativa confiavel disponivel.
	// Se a mitigacao ja estiver ligada neste primeiro ciclo, parte da
	// referencia "de confianca" (trusted_*) em vez do proprio GPS - assim,
	// se a primeira amostra ja vier de um ataque em andamento, o filtro nao
	// comeca contaminado por ela.
	if (!_kf_initialized) {
		_kf_n_m   = mitigation_enabled ? trusted_n : meas_n;
		_kf_e_m   = mitigation_enabled ? trusted_e : meas_e;
		_kf_alt   = mitigation_enabled ? trusted_alt : meas_alt;
		_kf_vel_n = vel_north;
		_kf_vel_e = vel_east;
		_kf_vel_d = vel_down;
		_kf_P_n   = JMIT_R_POS_CLEAN;
		_kf_P_e   = JMIT_R_POS_CLEAN;
		_kf_P_alt = JMIT_R_ALT_CLEAN;
		_kf_P_vn  = static_cast<float>(JMIT_R_VEL_CLEAN);
		_kf_P_ve  = static_cast<float>(JMIT_R_VEL_CLEAN);
		_kf_P_vd  = static_cast<float>(JMIT_R_VEL_CLEAN);
		_kf_attack_dur_us = 0;
		_kf_last_us       = now;
		_kf_initialized   = true;

		return false;
	}

	const double dt = math::constrain(
			      static_cast<double>(now - _kf_last_us) * 1e-6, 0.005, 0.25);
	_kf_last_us = now;

	// Predicao cinematica: onde o drone deveria estar agora, extrapolando
	// a ultima posicao conhecida pela velocidade estimada do filtro - o
	// "achismo" do KF contra o qual a nova medicao sera comparada.
	const double pred_n   = _kf_n_m  + static_cast<double>(_kf_vel_n) * dt;
	const double pred_e   = _kf_e_m  + static_cast<double>(_kf_vel_e) * dt;
	const double pred_alt = _kf_alt  - static_cast<double>(_kf_vel_d) * dt;

	// Covariancia predita: a incerteza da predicao cresce a cada ciclo
	// (ruido de processo Q), ate o teto JMIT_P_MAX - impede que a
	// incerteza "exploda" indefinidamente se muitas amostras seguidas
	// forem rejeitadas.
	const double P_n   = math::min(_kf_P_n   + JMIT_Q_POS, static_cast<double>(JMIT_P_MAX));
	const double P_e   = math::min(_kf_P_e   + JMIT_Q_POS, static_cast<double>(JMIT_P_MAX));
	const double P_alt = math::min(_kf_P_alt + JMIT_Q_ALT, static_cast<double>(JMIT_P_MAX));
	const float  P_vn  = math::min(_kf_P_vn  + static_cast<float>(JMIT_Q_VEL), JMIT_P_MAX);
	const float  P_ve  = math::min(_kf_P_ve  + static_cast<float>(JMIT_Q_VEL), JMIT_P_MAX);
	const float  P_vd  = math::min(_kf_P_vd  + static_cast<float>(JMIT_Q_VEL), JMIT_P_MAX);

	// Inovacao: diferenca entre medicao GPS recebida e predicao do filtro.
	// E o "erro de surpresa" - quanto maior, mais a medicao discorda do
	// que o filtro esperava ver.
	const double inn_n   = meas_n   - pred_n;
	const double inn_e   = meas_e   - pred_e;
	const double inn_alt = meas_alt - pred_alt;

	// NIS (Normalized Innovation Squared): a inovacao ao quadrado, dividida
	// pela incerteza combinada da predicao e da medicao. Normalizar dessa
	// forma e o que permite usar um limiar fixo (JMIT_NIS_THRESHOLD)
	// independente de quao "nervoso" ou "calmo" o filtro esteja no momento -
	// um NIS alto indica que a medicao e estatisticamente incompativel com
	// a predicao, nao apenas numericamente diferente.
	const double NIS_n   = (inn_n   * inn_n)   / (P_n   + JMIT_R_POS_CLEAN);
	const double NIS_e   = (inn_e   * inn_e)   / (P_e   + JMIT_R_POS_CLEAN);
	const double NIS_alt = (inn_alt * inn_alt) / (P_alt + JMIT_R_ALT_CLEAN);

	// Qualquer eixo (N, E ou altitude) passando do limiar ja e suficiente
	// para marcar a amostra inteira como anomala - um ataque raramente
	// afeta todos os eixos igualmente, entao exigir todos seria facil de burlar.
	const bool gps_anomalous =
		(NIS_n   > JMIT_NIS_THRESHOLD) ||
		(NIS_e   > JMIT_NIS_THRESHOLD) ||
		(NIS_alt > JMIT_NIS_THRESHOLD);
	gps_anomalous_now = gps_anomalous;

	if (mitigation_enabled && gps_anomalous) {
		const double max_nis = math::max(NIS_n, math::max(NIS_e, NIS_alt));

		if (max_nis >= AUTO_LAND_SEVERE_NIS_THRESHOLD) {
			_gps_anomaly_severe = true;
			_gps_anomaly_severe_us = now;
		}
	}

	// Se a mitigacao estiver ativa, medicoes anomalas sao rejeitadas: a
	// partir daqui, o GPS bruto e ignorado por completo e a saida vira a
	// pseudo-medicao (estrategia 2 do cabecalho do arquivo) - a mesma
	// logica de prioridade de fonte usada em tickBlackout(), so que aqui
	// disparada por NIS alto em vez de timeout de publicacao.
	if (mitigation_enabled && gps_anomalous) {

		// VIO e a fonte preferida por ser independente do GPS (nao pode
		// ser afetada pelo mesmo ataque). So e usada se a amostra for
		// recente o bastante e todos os componentes forem numeros finitos.
		const bool vio_recent_for_dr =
			ctx.vio_valid &&
			(now >= ctx.vio_timestamp) &&
			((now - ctx.vio_timestamp) <= BLACKOUT_VIO_TIMEOUT_US) &&
			PX4_ISFINITE(ctx.vio_n_m) &&
			PX4_ISFINITE(ctx.vio_e_m) &&
			PX4_ISFINITE(ctx.vio_alt_msl);

		const float dr_vel_n = vio_recent_for_dr ?
					math::constrain(ctx.vio_vel_n, -JMIT_DR_VEL_MAX_M_S, JMIT_DR_VEL_MAX_M_S) : 0.0f;

		const float dr_vel_e = vio_recent_for_dr ?
					math::constrain(ctx.vio_vel_e, -JMIT_DR_VEL_MAX_M_S, JMIT_DR_VEL_MAX_M_S) : 0.0f;

		const float dr_vel_d = vio_recent_for_dr ?
					math::constrain(ctx.vio_vel_d, -JMIT_DR_VEL_Z_MAX_M_S, JMIT_DR_VEL_Z_MAX_M_S) : 0.0f;

		// Na primeira amostra anomala da sequencia, inicializa a
		// pseudo-medicao a partir da melhor fonte disponivel na hora:
		// VIO, senao LPOS, senao mantem o proprio valor do filtro (_kf_*).
		if (_kf_attack_first) {
			_jmit_pub_n_m = vio_recent_for_dr ? ctx.vio_n_m : (lpos_xy_recent ? ctx.lpos_n_m : _kf_n_m);
			_jmit_pub_e_m = vio_recent_for_dr ? ctx.vio_e_m : (lpos_xy_recent ? ctx.lpos_e_m : _kf_e_m);
			_jmit_pub_alt = vio_recent_for_dr ? ctx.vio_alt_msl : trusted_alt;

			_jmit_pub_vel_n = dr_vel_n;
			_jmit_pub_vel_e = dr_vel_e;
			_jmit_pub_vel_d = dr_vel_d;

			_kf_attack_first = false;
		}

		_kf_attack_dur_us += static_cast<uint64_t>(dt * 1e6);

		if (vio_recent_for_dr) {
			_jmit_pub_n_m = ctx.vio_n_m;
			_jmit_pub_e_m = ctx.vio_e_m;
			_jmit_pub_alt = ctx.vio_alt_msl;

			_jmit_pub_vel_n = dr_vel_n;
			_jmit_pub_vel_e = dr_vel_e;
			_jmit_pub_vel_d = dr_vel_d;

		} else {
			_jmit_pub_vel_n = 0.0f;
			_jmit_pub_vel_e = 0.0f;
			_jmit_pub_vel_d = 0.0f;

			if (!PX4_ISFINITE(_jmit_pub_alt)) {
				_jmit_pub_alt = _kf_alt;
			}
		}

		_kf_n_m   = _jmit_pub_n_m;
		_kf_e_m   = _jmit_pub_e_m;
		_kf_alt   = _jmit_pub_alt;
		_kf_vel_n = _jmit_pub_vel_n;
		_kf_vel_e = _jmit_pub_vel_e;
		_kf_vel_d = _jmit_pub_vel_d;

		_kf_P_n   = JMIT_R_POS_CLEAN;
		_kf_P_e   = JMIT_R_POS_CLEAN;
		_kf_P_alt = JMIT_R_ALT_CLEAN;
		_kf_P_vn  = static_cast<float>(JMIT_R_VEL_CLEAN);
		_kf_P_ve  = static_cast<float>(JMIT_R_VEL_CLEAN);
		_kf_P_vd  = static_cast<float>(JMIT_R_VEL_CLEAN);

		static uint64_t _kf_log_last_us = 0;

		if (now - _kf_log_last_us > 1000000ULL) {
			_kf_log_last_us = now;
			PX4_INFO("\n[KF-Mitig] ANCHOR-DR | dur=%.1f s | NIS=(%.0f,%.0f,%.0f) | pos=(%.1f,%.1f) m | alt=%.2f | alt_src=%s | vel_src=%s | vel=(%.1f,%.1f,%.1f) m/s\n",
				 static_cast<double>(_kf_attack_dur_us) / 1e6,
				 NIS_n, NIS_e, NIS_alt,
				 _jmit_pub_n_m, _jmit_pub_e_m,
				 _jmit_pub_alt,
				 vio_recent_for_dr ? "VIO" : "HOLD",
				 vio_recent_for_dr ? "VIO" : "HOLD",
				 static_cast<double>(_jmit_pub_vel_n),
				 static_cast<double>(_jmit_pub_vel_e),
				 static_cast<double>(_jmit_pub_vel_d));
		}

		// Reprojecao da ancora local para latitude/longitude antes da publicacao GPS.
		double anchor_lat, anchor_lon;
		pos_ref.reproject(
			static_cast<float>(_jmit_pub_n_m),
			static_cast<float>(_jmit_pub_e_m),
			anchor_lat, anchor_lon);
		latitude  = anchor_lat;
		longitude = anchor_lon;
		altitude  = _jmit_pub_alt;
		vel_north = _jmit_pub_vel_n;
		vel_east  = _jmit_pub_vel_e;
		vel_down  = _jmit_pub_vel_d;

	} else {

		// Modo nominal: a medicao e aceita e usada para atualizar o filtro
		// via ganho de Kalman classico - o GPS "puxa" a estimativa do
		// filtro na direcao da medicao, na proporcao definida por quanto
		// o filtro confia em cada um (P vs R).
		_kf_attack_dur_us = 0;
		_kf_attack_first  = true;

		if (!_gps_landing_active && !_gps_auto_land_sent) {
			_gps_anomaly_severe = false;
			_gps_anomaly_severe_us = 0;
			_gps_land_source_severe_anomaly = false;
		}

		// Ganho de Kalman: quanto maior a incerteza da predicao (P) em
		// relacao a incerteza da medicao (R), mais peso a medicao recebe
		// na atualizacao - e o mecanismo que faz o filtro "confiar mais"
		// no GPS quando a propria predicao esta menos certa de si.
		const double K_n   = P_n   / (P_n   + JMIT_R_POS_CLEAN);
		const double K_e   = P_e   / (P_e   + JMIT_R_POS_CLEAN);
		const double K_alt = P_alt / (P_alt + JMIT_R_ALT_CLEAN);
		const float  K_vn  = P_vn  / (P_vn  + static_cast<float>(JMIT_R_VEL_CLEAN));
		const float  K_ve  = P_ve  / (P_ve  + static_cast<float>(JMIT_R_VEL_CLEAN));
		const float  K_vd  = P_vd  / (P_vd  + static_cast<float>(JMIT_R_VEL_CLEAN));

		const float  inn_vn  = vel_north - _kf_vel_n;
		const float  inn_ve  = vel_east  - _kf_vel_e;
		const float  inn_vd  = vel_down  - _kf_vel_d;

		_kf_n_m   = pred_n   + K_n   * inn_n;
		_kf_e_m   = pred_e   + K_e   * inn_e;
		_kf_alt   = pred_alt + K_alt * inn_alt;

		// ESTRATEGIA 3 (referencia cruzada com o barometro): so calibra o
		// offset entre a altitude do filtro (ja confiavel, pois a amostra
		// e nominal) e a altitude que o barometro esta reportando agora.
		// Esse offset e a "vara de medir" que a mitigacao de barometro usa
		// depois para saber se uma leitura de pressao esta condizente com
		// a realidade - nunca calibrado durante um ataque confirmado, para
		// nao aprender um offset contaminado pelo proprio ataque.
		if (ctx.jmit_baro_valid) {
			ctx.jmit_baro_alt_offset = _kf_alt - static_cast<double>(ctx.jmit_baro_alt_m);
			ctx.jmit_baro_alt_offset_valid = true;
		}

		_kf_vel_n = _kf_vel_n + K_vn * inn_vn;
		_kf_vel_e = _kf_vel_e + K_ve * inn_ve;
		_kf_vel_d = _kf_vel_d + K_vd * inn_vd;

		_kf_P_n   = (1.0  - K_n  ) * P_n;
		_kf_P_e   = (1.0  - K_e  ) * P_e;
		_kf_P_alt = (1.0  - K_alt) * P_alt;
		_kf_P_vn  = (1.0f - K_vn ) * P_vn;
		_kf_P_ve  = (1.0f - K_ve ) * P_ve;
		_kf_P_vd  = (1.0f - K_vd ) * P_vd;
	}

	return gps_anomalous_now;
}
// ======== MITIGACAO GPS - FIM ========

// ======== MITIGACAO BLACKOUT GPS - INICIO ========
// A mitigacao detecta blackout por sintoma: o GPS real ficou sem publicacao
// recente, enquanto o restante do sistema continua executando. Diferente de
// detectAndCorrect() (que reage a uma medicao presente mas suspeita), aqui
// nao ha medicao nenhuma para comparar - so o silencio do sensor.
bool GpsMitigation::tickBlackout(bool mitigation_enabled, uint64_t timestamp, MapProjection &pos_ref,
				  const GpsMitigationContext &ctx,
				  bool gps_real_valid, uint64_t gps_real_last_us,
				  double gps_real_n_m, double gps_real_e_m, double gps_real_alt,
				  float gps_real_vel_n, float gps_real_vel_e,
				  int nominal_satellites,
				  sensor_gps_s &out_pseudo_gps)
{
	const bool gps_real_recent =
		gps_real_valid &&
		((timestamp - gps_real_last_us) <= BLACKOUT_GPS_TIMEOUT_US);

	const bool blackout_detected =
		mitigation_enabled &&
		!_gps_auto_disarm_sent &&
		pos_ref.isInitialized() &&
		gps_real_valid &&
		!gps_real_recent;

	if (blackout_detected) {

		const bool lpos_recent =
			ctx.lpos_xy_valid &&
			ctx.lpos_z_valid &&
			(timestamp >= ctx.lpos_timestamp) &&
			((timestamp - ctx.lpos_timestamp) <= 500000ULL) &&
			PX4_ISFINITE(ctx.lpos_n_m) &&
			PX4_ISFINITE(ctx.lpos_e_m) &&
			PX4_ISFINITE(ctx.lpos_alt_msl);

		const bool vio_recent =
			ctx.vio_valid &&
			(timestamp >= ctx.vio_timestamp) &&
			((timestamp - ctx.vio_timestamp) <= BLACKOUT_VIO_TIMEOUT_US);

		if (!_blackout_anchor_initialized) {
			// Ancora inicial: escolhida uma unica vez, no primeiro ciclo do
			// blackout, seguindo a mesma prioridade VIO > LPOS > KF > ultimo
			// GPS real conhecido - da estrategia 2 do cabecalho do arquivo.
			const char *anchor_source = "LAST_GPS";

			if (vio_recent) {
				_blackout_anchor_n_m = ctx.vio_n_m;
				_blackout_anchor_e_m = ctx.vio_e_m;
				_blackout_anchor_alt = ctx.vio_alt_msl;
				_blackout_dr_vel_n   = ctx.vio_vel_n;
				_blackout_dr_vel_e   = ctx.vio_vel_e;
				_blackout_dr_vel_d   = 0.0f;
				anchor_source = "VIO";

			} else if (lpos_recent) {
				_blackout_anchor_n_m = ctx.lpos_n_m;
				_blackout_anchor_e_m = ctx.lpos_e_m;
				_blackout_anchor_alt = ctx.lpos_alt_msl;
				_blackout_dr_vel_n   = ctx.imu_dr_vel_n;
				_blackout_dr_vel_e   = ctx.imu_dr_vel_e;
				_blackout_dr_vel_d   = 0.0f;
				anchor_source = "LPOS";

			} else if (_kf_initialized) {
				_blackout_anchor_n_m = _kf_n_m;
				_blackout_anchor_e_m = _kf_e_m;
				_blackout_anchor_alt = _kf_alt;
				_blackout_dr_vel_n   = _kf_vel_n;
				_blackout_dr_vel_e   = _kf_vel_e;
				_blackout_dr_vel_d   = 0.0f;
				anchor_source = "KF";

			} else {
				_blackout_anchor_n_m = gps_real_n_m;
				_blackout_anchor_e_m = gps_real_e_m;
				_blackout_anchor_alt = gps_real_alt;
				_blackout_dr_vel_n   = gps_real_vel_n;
				_blackout_dr_vel_e   = gps_real_vel_e;
				_blackout_dr_vel_d   = 0.0f;
			}

			_blackout_dr_n_m      = _blackout_anchor_n_m;
			_blackout_dr_e_m      = _blackout_anchor_e_m;
			_blackout_dr_alt      = _blackout_anchor_alt;
			_blackout_start_us    = timestamp;
			_blackout_last_dr_us  = timestamp;
			_blackout_last_pub_us = 0;
			_blackout_anchor_initialized = true;
			_blackout_detected = true;

			PX4_WARN("\n[Blackout-Mitig] Blackout detected by GPS timeout: %.0f ms | anchor N=%.1f E=%.1f Alt=%.1f | source=%s\n",
				 static_cast<double>(timestamp - gps_real_last_us) / 1000.0,
				 _blackout_dr_n_m, _blackout_dr_e_m, _blackout_dr_alt,
				 anchor_source);
		}

		if (vio_recent) {
			_blackout_dr_n_m   = ctx.vio_n_m;
			_blackout_dr_e_m   = ctx.vio_e_m;
			_blackout_dr_alt   = ctx.vio_alt_msl;
			_blackout_dr_vel_n = ctx.vio_vel_n;
			_blackout_dr_vel_e = ctx.vio_vel_e;
			_blackout_dr_vel_d = ctx.vio_vel_d;
			_blackout_last_dr_us = timestamp;

		} else if (lpos_recent) {
			_blackout_dr_n_m   = ctx.lpos_n_m;
			_blackout_dr_e_m   = ctx.lpos_e_m;
			_blackout_dr_alt   = ctx.lpos_alt_msl;
			_blackout_dr_vel_n = ctx.imu_dr_vel_n;
			_blackout_dr_vel_e = ctx.imu_dr_vel_e;
			_blackout_dr_vel_d = ctx.imu_dr_vel_d;
			_blackout_last_dr_us = timestamp;

		} else {
			const double dt_dr = math::constrain(
					static_cast<double>(timestamp - _blackout_last_dr_us) * 1e-6,
					0.0, 0.1);

			_blackout_last_dr_us = timestamp;

			_blackout_dr_vel_n = math::constrain(_blackout_dr_vel_n, -JMIT_DR_VEL_MAX_M_S, JMIT_DR_VEL_MAX_M_S);
			_blackout_dr_vel_e = math::constrain(_blackout_dr_vel_e, -JMIT_DR_VEL_MAX_M_S, JMIT_DR_VEL_MAX_M_S);
			_blackout_dr_vel_d = 0.0f;

			_blackout_dr_n_m += static_cast<double>(_blackout_dr_vel_n) * dt_dr;
			_blackout_dr_e_m += static_cast<double>(_blackout_dr_vel_e) * dt_dr;

			constexpr float DR_VEL_DECAY_PER_STEP = 0.995f;
			_blackout_dr_vel_n *= DR_VEL_DECAY_PER_STEP;
			_blackout_dr_vel_e *= DR_VEL_DECAY_PER_STEP;

			if (fabsf(_blackout_dr_vel_n) < 0.02f) { _blackout_dr_vel_n = 0.0f; }
			if (fabsf(_blackout_dr_vel_e) < 0.02f) { _blackout_dr_vel_e = 0.0f; }

			if (!PX4_ISFINITE(_blackout_dr_alt)) {
				_blackout_dr_alt = _blackout_anchor_alt;
			}
		}

		const bool vio_alt_recent =
			ctx.vio_valid &&
			(timestamp >= ctx.vio_timestamp) &&
			((timestamp - ctx.vio_timestamp) <= BLACKOUT_VIO_TIMEOUT_US) &&
			PX4_ISFINITE(ctx.vio_alt_msl);

		if (!_gps_auto_disarm_sent) {
			double aux_alt_msl = _blackout_dr_alt;
			float aux_vel_d = _blackout_dr_vel_d;
			const char *aux_alt_src = "DR";

			if (vio_alt_recent) {
				aux_alt_msl = ctx.vio_alt_msl;
				aux_vel_d = ctx.vio_vel_d;
				aux_alt_src = "VIO";
			}

			if (PX4_ISFINITE(aux_alt_msl)) {
				if (_gps_landing_active) {
					aux_alt_msl = math::max(_gps_land_target_alt_msl, aux_alt_msl);
				}

				_blackout_dr_alt = aux_alt_msl;
				_blackout_dr_vel_d = math::constrain(aux_vel_d, -JMIT_DR_VEL_Z_MAX_M_S, JMIT_DR_VEL_Z_MAX_M_S);

				static uint64_t blackout_alt_sync_log_last_us = 0;

				if ((timestamp - blackout_alt_sync_log_last_us) > 1000000ULL) {
					blackout_alt_sync_log_last_us = timestamp;
					PX4_INFO("\n[Blackout-Mitig] Alt sync | src=%s | alt=%.2f | vio_alt=%.2f\n",
						 aux_alt_src, _blackout_dr_alt,
						 vio_alt_recent ? ctx.vio_alt_msl : -1.0);
				}
			}
		}

		// Durante o pouso mitigado, mantem N/E fixos e so reduz altitude se pitch/roll estiverem baixos.
		if (_gps_landing_active) {
			const bool land_att_recent =
				ctx.sim_att_valid &&
				(timestamp >= ctx.sim_att_timestamp) &&
				((timestamp - ctx.sim_att_timestamp) <= 500000ULL);

			const float land_abs_pitch_rad = land_att_recent ? fabsf(ctx.sim_pitch_rad) : 0.0f;
			const float land_abs_roll_rad  = land_att_recent ? fabsf(ctx.sim_roll_rad) : 0.0f;

			const bool descent_attitude_ok =
				!_gps_land_source_blackout ||
				!land_att_recent ||
				((land_abs_pitch_rad <= AUTO_LAND_MAX_PITCH_RAD) &&
				 (land_abs_roll_rad <= AUTO_LAND_MAX_ROLL_RAD));

			double land_alt_msl = _gps_land_last_alt_valid ? _gps_land_last_alt_msl : _gps_land_start_alt_msl;
			float land_vel_d = 0.0f;

			// Se VIO estiver disponivel, usa a altitude dela como limite -
			// nunca deixa a altitude estimada subir acima do que a VIO
			// reporta, mesmo que a taxa de descida sugerisse mais.
			if (vio_alt_recent) {
				const double aux_land_alt_msl = ctx.vio_alt_msl;

				if (PX4_ISFINITE(aux_land_alt_msl)) {
					land_alt_msl = math::max(
							       _gps_land_target_alt_msl,
							       math::min(land_alt_msl, aux_land_alt_msl));
				}
			}

			const double dt_land_s =
				(_gps_land_last_update_us > 0 && timestamp >= _gps_land_last_update_us) ?
				math::constrain(static_cast<double>(timestamp - _gps_land_last_update_us) * 1e-6, 0.0, 0.1) :
				0.0;

			_gps_land_last_update_us = timestamp;

			// Desce a taxa constante enquanto a atitude permitir.
			if (descent_attitude_ok && !_gps_auto_disarm_sent) {
				land_alt_msl = math::max(
						       _gps_land_target_alt_msl,
						       land_alt_msl - (AUTO_LAND_DESCENT_RATE_M_S * dt_land_s));

				land_vel_d = (land_alt_msl <= _gps_land_target_alt_msl) ?
					     0.0f : static_cast<float>(AUTO_LAND_DESCENT_RATE_M_S);
			}

			// Apos o desarme, trava na altitude-alvo.
			if (_gps_auto_disarm_sent) {
				land_alt_msl = _gps_land_target_alt_msl;
				land_vel_d = 0.0f;
			}

			_gps_land_last_alt_msl = land_alt_msl;
			_gps_land_last_alt_valid = true;

			// Posicao horizontal durante o pouso: VIO, senao LPOS, senao
			// mantem a posicao de pouso fixa (sem velocidade).
			if (vio_recent) {
				_blackout_dr_n_m = ctx.vio_n_m;
				_blackout_dr_e_m = ctx.vio_e_m;
				_blackout_dr_vel_n = ctx.vio_vel_n;
				_blackout_dr_vel_e = ctx.vio_vel_e;

			} else if (lpos_recent) {
				_blackout_dr_n_m = ctx.lpos_n_m;
				_blackout_dr_e_m = ctx.lpos_e_m;
				_blackout_dr_vel_n = ctx.imu_dr_vel_n;
				_blackout_dr_vel_e = ctx.imu_dr_vel_e;

			} else {
				_blackout_dr_n_m = _gps_land_hold_n_m;
				_blackout_dr_e_m = _gps_land_hold_e_m;
				_blackout_dr_vel_n = 0.0f;
				_blackout_dr_vel_e = 0.0f;
			}

			_blackout_dr_alt = land_alt_msl;
			_blackout_dr_vel_d = descent_attitude_ok ? land_vel_d : 0.0f;

			static uint64_t pitch_hold_log_last_us = 0;

			if (!descent_attitude_ok &&
			    ((timestamp - pitch_hold_log_last_us) > AUTO_LAND_ATT_LOG_INTERVAL_US)) {
				pitch_hold_log_last_us = timestamp;
				PX4_INFO("\n[GPS-Land] Holding descent due attitude | pitch=%.1f deg | roll=%.1f deg | alt=%.2f\n",
					 land_att_recent ? static_cast<double>(ctx.sim_pitch_rad * 57.2957795f) : -999.0,
					 land_att_recent ? static_cast<double>(ctx.sim_roll_rad * 57.2957795f) : -999.0,
					 land_alt_msl);
			}
		}

		// Publica pseudo-GPS em 10 Hz durante a ausencia do GPS real.
		if (!_gps_auto_disarm_sent &&
		    (timestamp - _blackout_last_pub_us) >= BLACKOUT_PUB_INTERVAL_US) {

			const uint64_t blackout_elapsed_us = timestamp - _blackout_start_us;

			if (blackout_elapsed_us > BLACKOUT_MAX_HOLD_US) {
				PX4_WARN("\n[Blackout-Mitig] Maximum dead-reckoning time reached. Pseudo-GPS stopped.\n");
				return false;
			}

			double p_lat = 0.0, p_lon = 0.0;
			pos_ref.reproject(static_cast<float>(_blackout_dr_n_m), static_cast<float>(_blackout_dr_e_m),
					   p_lat, p_lon);

			device::Device::DeviceId id{};
			id.devid_s.bus_type = device::Device::DeviceBusType::DeviceBusType_SIMULATION;
			id.devid_s.devtype = DRV_GPS_DEVTYPE_SIM;
			id.devid_s.bus = 1;
			id.devid_s.address = 1;

			out_pseudo_gps = sensor_gps_s{};
			out_pseudo_gps.timestamp = timestamp;
			out_pseudo_gps.timestamp_sample = timestamp;
			out_pseudo_gps.device_id = id.devid;
			out_pseudo_gps.latitude_deg = p_lat;
			out_pseudo_gps.longitude_deg = p_lon;
			out_pseudo_gps.altitude_msl_m = _blackout_dr_alt;
			out_pseudo_gps.altitude_ellipsoid_m = _blackout_dr_alt;
			out_pseudo_gps.vel_n_m_s = _blackout_dr_vel_n;
			out_pseudo_gps.vel_e_m_s = _blackout_dr_vel_e;
			out_pseudo_gps.vel_d_m_s = _blackout_dr_vel_d;
			out_pseudo_gps.vel_m_s = sqrtf(_blackout_dr_vel_n * _blackout_dr_vel_n +
							_blackout_dr_vel_e * _blackout_dr_vel_e);

			if (_gps_landing_active && _gps_land_heading_valid) {
				out_pseudo_gps.cog_rad = _gps_land_heading_rad;
			} else {
				out_pseudo_gps.cog_rad = atan2f(_blackout_dr_vel_e, _blackout_dr_vel_n);
			}
			out_pseudo_gps.vel_ned_valid = true;

			out_pseudo_gps.fix_type = 3;
			out_pseudo_gps.satellites_used = static_cast<uint8_t>(math::max(nominal_satellites, 4));

			if (_gps_landing_active) {
				out_pseudo_gps.eph  = 1.0f;
				out_pseudo_gps.epv  = 1.5f;
				out_pseudo_gps.hdop = 0.8f;
				out_pseudo_gps.vdop = 1.0f;
			} else {
				out_pseudo_gps.eph  = BLACKOUT_EPH_DEGRADE;
				out_pseudo_gps.epv  = BLACKOUT_EPV_DEGRADE;
				out_pseudo_gps.hdop = BLACKOUT_HDOP_DEGRADE;
				out_pseudo_gps.vdop = BLACKOUT_VDOP_DEGRADE;
			}

			out_pseudo_gps.jamming_indicator = 0;
			out_pseudo_gps.jamming_state = 0;

			_blackout_last_pub_us = timestamp;

			static uint64_t blackout_log_last_us = 0;

			if ((timestamp - blackout_log_last_us) > 1000000ULL) {
				blackout_log_last_us = timestamp;
				const bool vio_recent_log =
					ctx.vio_valid && ((timestamp - ctx.vio_timestamp) <= BLACKOUT_VIO_TIMEOUT_US);
				PX4_INFO("\n[Blackout-Mitig] Pseudo-GPS | src=%s | t=%.1f s | N=%.1f E=%.1f Alt=%.1f | Vel=(%.1f, %.1f, %.1f)\n",
					 vio_recent_log ? "VIO" : "DR-HOR",
					 static_cast<double>(blackout_elapsed_us) / 1e6,
					 _blackout_dr_n_m, _blackout_dr_e_m, _blackout_dr_alt,
					 static_cast<double>(_blackout_dr_vel_n),
					 static_cast<double>(_blackout_dr_vel_e),
					 static_cast<double>(_blackout_dr_vel_d));
			}

			return true;
		}

	} else if (_blackout_anchor_initialized && gps_real_recent) {
		_blackout_anchor_initialized = false;
		_blackout_detected = false;
		PX4_INFO("\n[Blackout-Mitig] Real GPS recovered by timeout. Pseudo-GPS stopped.\n");
	}

	return false;
}
// ======== MITIGACAO BLACKOUT GPS - FIM ========

// ======== MITIGACAO DE POUSO GPS - INICIO ========
// Controla o pouso quando a mitigacao GPS esta ativa.
// A origem pode ser blackout ou anomalia GPS persistente rejeitada pelo filtro
// (as duas usam criterios de chegada diferentes - blackout e mais permissivo,
// ver AUTO_LAND_DEST_RADIUS_M/AUTO_LAND_ANOMALY_DEST_RADIUS_M).
void GpsMitigation::checkAutoLand(bool mitigation_enabled, uint64_t timestamp, MapProjection &pos_ref, float alt_ref,
				   const GpsMitigationContext &ctx, double gps_real_alt)
{
	// So roda com a mitigacao ligada, antes do pouso ja ter sido enviado,
	// e com a projecao de coordenadas inicializada.
	if (!mitigation_enabled || _gps_auto_land_sent || !pos_ref.isInitialized()) {
		_gps_auto_land_arrival_us = 0;
		_gps_auto_land_last_arrived_us = 0;
		return;
	}

	// Decide qual e a origem do gatilho: blackout tem prioridade sobre
	// anomalia por ruido.
	const bool blackout_active = _blackout_anchor_initialized && _blackout_detected;
	const bool gps_anomaly_active = _kf_initialized && (_kf_attack_dur_us >= AUTO_LAND_MIN_NOISE_US);

	bool use_blackout_source = false;

	if (blackout_active) {
		// So considera blackout como gatilho apos ele durar pelo menos
		// AUTO_LAND_MIN_BLACKOUT_US - evita reagir a uma falha momentanea.
		if ((timestamp - _blackout_start_us) < AUTO_LAND_MIN_BLACKOUT_US) {
			return;
		}

		use_blackout_source = true;

	} else if (gps_anomaly_active) {
		use_blackout_source = false;

	} else {
		// Nenhum ataque ativo no momento: mantem o cronometro de chegada
		// so se a ultima chegada foi recente (tolera pulsos curtos de
		// jamming sem reiniciar o progresso do zero); caso contrario, zera.
		const bool keep_arrival_timer =
			(_gps_auto_land_arrival_us > 0) &&
			(_gps_auto_land_last_arrived_us > 0) &&
			(timestamp >= _gps_auto_land_last_arrived_us) &&
			((timestamp - _gps_auto_land_last_arrived_us) <= AUTO_LAND_PULSE_BRIDGE_US);

		if (!keep_arrival_timer) {
			_gps_auto_land_arrival_us = 0;
			_gps_auto_land_last_arrived_us = 0;
		}

		return;
	}

	// Precisa de LPOS recente para calcular a distancia ate o destino.
	if (!ctx.lpos_xy_valid || (timestamp - ctx.lpos_timestamp) > 500000ULL) {
		_gps_auto_land_arrival_us = 0;
		_gps_auto_land_last_arrived_us = 0;
		return;
	}

	// Le o destino atual da missao (setpoint de posicao) e projeta em
	// coordenadas locais para comparar com a posicao estimada do drone.
	position_setpoint_triplet_s triplet{};

	if (!_pos_sp_triplet_sub.copy(&triplet) || !triplet.current.valid ||
	    !PX4_ISFINITE(triplet.current.lat) || !PX4_ISFINITE(triplet.current.lon)) {
		_gps_auto_land_arrival_us = 0;
		_gps_auto_land_last_arrived_us = 0;
		return;
	}

	float dest_n_m = 0.0f;
	float dest_e_m = 0.0f;
	pos_ref.project(triplet.current.lat, triplet.current.lon, dest_n_m, dest_e_m);

	// Anomalia "severa" (NIS muito acima do normal) usa raio/velocidade
	// diferentes dos demais casos.
	const bool severe_anomaly_recent =
		!use_blackout_source &&
		_gps_anomaly_severe &&
		(_kf_attack_dur_us >= AUTO_LAND_MIN_NOISE_US);

	// Estimativa de posicao usada para o calculo de distancia: LPOS por
	// padrao, ou a fonte especifica do ataque em curso (dead-reckoning do
	// blackout, ou pseudo-medicao da anomalia por ruido).
	double land_est_n_m = ctx.lpos_n_m;
	double land_est_e_m = ctx.lpos_e_m;
	float land_est_speed_m_s = ctx.lpos_ground_speed;

	if (use_blackout_source) {
		land_est_n_m = _blackout_dr_n_m;
		land_est_e_m = _blackout_dr_e_m;
		land_est_speed_m_s = sqrtf(_blackout_dr_vel_n * _blackout_dr_vel_n +
					    _blackout_dr_vel_e * _blackout_dr_vel_e);

	} else if (_kf_attack_dur_us > 0 &&
		   PX4_ISFINITE(_jmit_pub_n_m) && PX4_ISFINITE(_jmit_pub_e_m)) {
		land_est_n_m = _jmit_pub_n_m;
		land_est_e_m = _jmit_pub_e_m;
		land_est_speed_m_s = sqrtf(_jmit_pub_vel_n * _jmit_pub_vel_n +
					    _jmit_pub_vel_e * _jmit_pub_vel_e);
	}

	// Distancia horizontal ate o destino da missao.
	const double dn = land_est_n_m - static_cast<double>(dest_n_m);
	const double de = land_est_e_m - static_cast<double>(dest_e_m);
	const float horizontal_error = static_cast<float>(sqrt(dn * dn + de * de));

	// Raio de chegada e velocidade maxima aceitos, variando por origem
	// (blackout / anomalia severa / anomalia normal).
	const float land_radius_m = use_blackout_source ?
				     AUTO_LAND_DEST_RADIUS_M :
				     (severe_anomaly_recent ? AUTO_LAND_SEVERE_ANOMALY_DEST_RADIUS_M : AUTO_LAND_ANOMALY_DEST_RADIUS_M);

	const float land_max_speed_m_s = use_blackout_source ?
					  AUTO_LAND_MAX_SPEED_M_S :
					  (severe_anomaly_recent ? AUTO_LAND_SEVERE_ANOMALY_MAX_SPEED_M_S : AUTO_LAND_ANOMALY_MAX_SPEED_M_S);

	const bool arrived =
		horizontal_error <= land_radius_m &&
		land_est_speed_m_s <= land_max_speed_m_s;

	const uint64_t stable_required_us = use_blackout_source ?
					     AUTO_LAND_STABLE_US :
					     (severe_anomaly_recent ? AUTO_LAND_SEVERE_STABLE_US : AUTO_LAND_STABLE_US);

	if (!arrived) {
		_gps_auto_land_arrival_us = 0;
		_gps_auto_land_last_arrived_us = 0;
		return;
	}

	_gps_auto_land_last_arrived_us = timestamp;

	// So loga aviso de inclinacao alta durante blackout (nao bloqueia o
	// pouso, so avisa - a checagem que efetivamente controla a descida
	// esta em applyLandingOverride()).
	const bool preland_att_recent =
		ctx.sim_att_valid &&
		(timestamp >= ctx.sim_att_timestamp) &&
		((timestamp - ctx.sim_att_timestamp) <= 500000ULL);

	const float preland_abs_pitch_rad = preland_att_recent ? fabsf(ctx.sim_pitch_rad) : 0.0f;
	const float preland_abs_roll_rad  = preland_att_recent ? fabsf(ctx.sim_roll_rad) : 0.0f;

	const bool preland_tilt_high =
		use_blackout_source &&
		preland_att_recent &&
		((preland_abs_pitch_rad > AUTO_LAND_MAX_PITCH_RAD) ||
		 (preland_abs_roll_rad > AUTO_LAND_MAX_ROLL_RAD));

	if (preland_tilt_high) {
		static uint64_t preland_tilt_log_last_us = 0;

		if ((timestamp - preland_tilt_log_last_us) > AUTO_LAND_ATT_LOG_INTERVAL_US) {
			preland_tilt_log_last_us = timestamp;
			PX4_WARN("\n[GPS-Land] Tilt observed before LAND, but not blocking descent | pitch=%.1f deg | roll=%.1f deg | err_xy=%.1f m | vel=%.2f m/s\n",
				 static_cast<double>(ctx.sim_pitch_rad * 57.2957795f),
				 static_cast<double>(ctx.sim_roll_rad * 57.2957795f),
				 static_cast<double>(horizontal_error),
				 static_cast<double>(land_est_speed_m_s));
		}
	}

	// Primeira vez que a chegada e detectada: guarda a posicao e altitude
	// de referencia para o pouso, e inicia o cronometro de estabilidade.
	if (_gps_auto_land_arrival_us == 0) {
		_gps_auto_land_arrival_us = timestamp;
		_gps_land_source_blackout = use_blackout_source;
		_gps_land_source_severe_anomaly = severe_anomaly_recent;
		_gps_land_hold_n_m = land_est_n_m;
		_gps_land_hold_e_m = land_est_e_m;

		_gps_land_heading_rad = ctx.lpos_heading_rad;
		_gps_land_heading_valid = PX4_ISFINITE(ctx.lpos_heading_rad);

		if (!use_blackout_source) {
			// Altitude de partida do pouso: pseudo-medicao, senao o
			// proprio filtro, senao o ultimo GPS real conhecido.
			if (PX4_ISFINITE(_jmit_pub_alt) && _kf_attack_dur_us > 0) {
				_gps_land_start_alt_msl = _jmit_pub_alt;
			} else if (PX4_ISFINITE(_kf_alt)) {
				_gps_land_start_alt_msl = _kf_alt;
			} else {
				_gps_land_start_alt_msl = gps_real_alt;
			}

			_gps_land_target_alt_msl = static_cast<double>(alt_ref) + AUTO_LAND_TARGET_GPS_ANOMALY_AGL_M;

			if (_gps_land_start_alt_msl < _gps_land_target_alt_msl) {
				_gps_land_start_alt_msl = _gps_land_target_alt_msl;
			}

			_gps_land_last_alt_msl = _gps_land_start_alt_msl;
			_gps_land_last_alt_valid = true;
		}

		PX4_INFO("\n[GPS-Land] Destination reached | src=%s | err_xy=%.1f m | vel=%.2f m/s | window=%.1f s\n",
			 use_blackout_source ? "blackout" : "anomaly",
			 static_cast<double>(horizontal_error),
			 static_cast<double>(land_est_speed_m_s),
			 static_cast<double>(stable_required_us) / 1e6);

		if (stable_required_us > 0ULL) {
			return;
		}
	}

	// So confirma o pouso apos permanecer no destino pela janela de
	// estabilidade exigida (evita disparo por uma unica amostra).
	const uint64_t preland_elapsed_us = timestamp - _gps_auto_land_arrival_us;

	if (preland_elapsed_us < stable_required_us) {
		return;
	}

	// Para anomalia severa, exige tambem uma checagem final de velocidade
	// (com timeout de seguranca) antes de confirmar - proteciona extra
	// contra falso positivo num caso mais critico.
	const bool final_speed_check_needed = !use_blackout_source && severe_anomaly_recent;

	if (final_speed_check_needed) {
		const bool lpos_speed_recent =
			ctx.lpos_xy_valid &&
			(timestamp >= ctx.lpos_timestamp) &&
			((timestamp - ctx.lpos_timestamp) <= 500000ULL) &&
			PX4_ISFINITE(ctx.lpos_ground_speed);

		const float final_speed_m_s = lpos_speed_recent ? ctx.lpos_ground_speed : land_est_speed_m_s;
		const bool final_wait_timeout = preland_elapsed_us >= AUTO_LAND_FINAL_MAX_WAIT_US;

		if ((final_speed_m_s > AUTO_LAND_FINAL_MAX_SPEED_M_S) && !final_wait_timeout) {
			return;
		}
	}

	// Confirma o pouso: fixa a posicao/altitude-alvo e envia o comando LAND.
	_gps_landing_active = true;
	_gps_auto_land_sent = true;
	_gps_land_source_blackout = use_blackout_source;
	_gps_land_source_severe_anomaly = (!use_blackout_source) && (_gps_land_source_severe_anomaly || severe_anomaly_recent);
	_gps_land_hold_start_us = timestamp;

	if (use_blackout_source) {
		_gps_land_hold_n_m = land_est_n_m;
		_gps_land_hold_e_m = land_est_e_m;
	}

	// Altitude de partida e altitude-alvo do pouso, com regras proprias
	// para blackout versus anomalia por ruido.
	if (use_blackout_source) {
		_gps_land_start_alt_msl = PX4_ISFINITE(_blackout_dr_alt) ? _blackout_dr_alt : _blackout_anchor_alt;
		_gps_land_target_alt_msl = static_cast<double>(alt_ref) + AUTO_LAND_TARGET_AGL_M;
	} else {
		if (_gps_land_last_alt_valid && PX4_ISFINITE(_gps_land_last_alt_msl)) {
			_gps_land_start_alt_msl = _gps_land_last_alt_msl;
		} else if (PX4_ISFINITE(_jmit_pub_alt) && _kf_attack_dur_us > 0) {
			_gps_land_start_alt_msl = _jmit_pub_alt;
		} else if (PX4_ISFINITE(_kf_alt)) {
			_gps_land_start_alt_msl = _kf_alt;
		} else {
			_gps_land_start_alt_msl = gps_real_alt;
		}

		_gps_land_target_alt_msl = static_cast<double>(alt_ref) + AUTO_LAND_TARGET_GPS_ANOMALY_AGL_M;
	}

	if (_gps_land_start_alt_msl < _gps_land_target_alt_msl) {
		_gps_land_start_alt_msl = _gps_land_target_alt_msl;
	}

	// No blackout, ja comeca descendo (velocidade vertical inicial > 0);
	// na anomalia por ruido, applyLandingOverride() controla a descida
	// gradual a partir daqui.
	const float initial_land_vel_d = use_blackout_source ? static_cast<float>(AUTO_LAND_DESCENT_RATE_M_S) : 0.0f;

	// Sincroniza pseudo-medicao, filtro e dead-reckoning com a posicao de pouso.
	_jmit_pub_n_m = _gps_land_hold_n_m;
	_jmit_pub_e_m = _gps_land_hold_e_m;
	_jmit_pub_alt = _gps_land_start_alt_msl;
	_jmit_pub_vel_n = 0.0f;
	_jmit_pub_vel_e = 0.0f;
	_jmit_pub_vel_d = initial_land_vel_d;

	_kf_n_m = _jmit_pub_n_m;
	_kf_e_m = _jmit_pub_e_m;
	_kf_alt = _jmit_pub_alt;
	_kf_vel_n = 0.0f;
	_kf_vel_e = 0.0f;
	_kf_vel_d = initial_land_vel_d;

	_blackout_dr_n_m = _gps_land_hold_n_m;
	_blackout_dr_e_m = _gps_land_hold_e_m;
	_blackout_dr_alt = _gps_land_start_alt_msl;
	_blackout_dr_vel_n = 0.0f;
	_blackout_dr_vel_e = 0.0f;
	_blackout_dr_vel_d = initial_land_vel_d;

	_gps_land_last_alt_msl = _gps_land_start_alt_msl;
	_gps_land_last_alt_valid = true;
	_gps_land_last_update_us = timestamp;

	publishLandCommand(timestamp, pos_ref);
}

void GpsMitigation::applyLandingOverride(uint64_t timestamp, MapProjection &pos_ref, float alt_ref,
					  const GpsMitigationContext &ctx,
					  double &latitude, double &longitude, double &altitude,
					  float &vel_north, float &vel_east, float &vel_down,
					  bool &gps_anomalous_now)
{
	// Fase de "pre-pouso": o drone ja chegou perto do destino (checkAutoLand
	// ja comecou a contar o tempo de estabilidade), mas o pouso oficial
	// ainda nao foi confirmado. Enquanto isso, mantem a posicao/altitude
	// congeladas no ponto de chegada (nao deixa o veiculo se mover) -
	// aplica-se so ao caso de anomalia por ruido (blackout tem seu proprio
	// fluxo de chegada, sem essa fase intermediaria).
	const bool anomaly_preland_active =
		!_gps_landing_active &&
		!_gps_auto_land_sent &&
		(_gps_auto_land_arrival_us != 0) &&
		_kf_initialized &&
		(_kf_attack_dur_us >= AUTO_LAND_MIN_NOISE_US) &&
		!_blackout_anchor_initialized;

	if (anomaly_preland_active) {
		// Mantem a ultima altitude de pouso conhecida (se houver) em vez
		// de voltar para a altitude inicial - evita um salto de altitude
		// caso esta funcao seja chamada varias vezes antes da confirmacao final.
		double preland_alt_msl = _gps_land_start_alt_msl;

		if (_gps_land_last_alt_valid && PX4_ISFINITE(_gps_land_last_alt_msl)) {
			preland_alt_msl = _gps_land_last_alt_msl;
		}

		// Trava tanto a pseudo-medicao (_jmit_pub_*) quanto o proprio
		// estado do filtro de Kalman (_kf_*) na posicao de chegada -
		// assim, se o pouso for cancelado por algum motivo, o filtro nao
		// fica com um estado inconsistente com o que foi publicado.
		_jmit_pub_n_m = _gps_land_hold_n_m;
		_jmit_pub_e_m = _gps_land_hold_e_m;
		_jmit_pub_alt = preland_alt_msl;
		_jmit_pub_vel_n = 0.0f;
		_jmit_pub_vel_e = 0.0f;
		_jmit_pub_vel_d = 0.0f;

		_kf_n_m = _jmit_pub_n_m;
		_kf_e_m = _jmit_pub_e_m;
		_kf_alt = _jmit_pub_alt;
		_kf_vel_n = 0.0f;
		_kf_vel_e = 0.0f;
		_kf_vel_d = 0.0f;

		double hold_lat = 0.0, hold_lon = 0.0;
		pos_ref.reproject(static_cast<float>(_gps_land_hold_n_m), static_cast<float>(_gps_land_hold_e_m),
				   hold_lat, hold_lon);

		latitude = hold_lat;
		longitude = hold_lon;
		altitude = preland_alt_msl;
		vel_north = 0.0f;
		vel_east = 0.0f;
		vel_down = 0.0f;
		// Reafirma a anomalia mesmo que o NIS desta amostra especifica
		// tenha vindo baixo - enquanto o pre-pouso estiver ativo, a saida
		// publicada continua sendo a pseudo-medicao, nunca o GPS bruto.
		gps_anomalous_now = true;
	}

	// Fase de pouso confirmado: descida controlada ate a altitude-alvo,
	// mantendo N/E fixos (so a altitude muda).
	if (_gps_landing_active) {
		// So desce se a atitude estiver dentro de limites seguros - um
		// drone muito inclinado nao deve continuar descendo as cegas.
		const bool land_att_recent =
			ctx.sim_att_valid &&
			(timestamp >= ctx.sim_att_timestamp) &&
			((timestamp - ctx.sim_att_timestamp) <= 500000ULL);

		const float land_abs_pitch_rad = land_att_recent ? fabsf(ctx.sim_pitch_rad) : 0.0f;
		const float land_abs_roll_rad  = land_att_recent ? fabsf(ctx.sim_roll_rad) : 0.0f;

		const bool descent_attitude_ok =
			!land_att_recent ||
			((land_abs_pitch_rad <= AUTO_LAND_MAX_PITCH_RAD) &&
			 (land_abs_roll_rad <= AUTO_LAND_MAX_ROLL_RAD));

		double land_alt_msl = _gps_land_last_alt_valid ? _gps_land_last_alt_msl : _gps_land_start_alt_msl;
		float land_vel_d = 0.0f;

		// Altitude-alvo depende da origem do pouso: blackout ja definiu
		// um alvo fixo em checkAutoLand(); anomalia por ruido calcula a
		// partir da altitude de referencia atual (alt_ref pode ter mudado
		// desde o inicio do voo, ex. calibracao inicial diferente).
		const double target_alt_msl = _gps_land_source_blackout ?
					       _gps_land_target_alt_msl :
					       static_cast<double>(alt_ref) + AUTO_LAND_TARGET_GPS_ANOMALY_AGL_M;

		// Intervalo de tempo desde a ultima atualizacao, usado para
		// integrar a taxa de descida constante (AUTO_LAND_DESCENT_RATE_M_S)
		// - limitado a 0.1s para nao dar um "salto" grande de altitude se
		// esta funcao ficar muito tempo sem ser chamada.
		const double dt_land_s =
			(_gps_land_last_update_us > 0 && timestamp >= _gps_land_last_update_us) ?
			math::constrain(static_cast<double>(timestamp - _gps_land_last_update_us) * 1e-6, 0.0, 0.1) :
			0.0;

		_gps_land_last_update_us = timestamp;

		// Desce a taxa constante enquanto a atitude permitir e o desarme
		// ainda nao foi enviado; ao alcancar a altitude-alvo, para de
		// descer (velocidade vertical zerada).
		if (descent_attitude_ok && !_gps_auto_disarm_sent) {
			land_alt_msl = math::max(
					       target_alt_msl,
					       land_alt_msl - (AUTO_LAND_DESCENT_RATE_M_S * dt_land_s));

			land_vel_d = (land_alt_msl <= target_alt_msl) ?
				     0.0f : static_cast<float>(AUTO_LAND_DESCENT_RATE_M_S);
		}

		// Apos o desarme, trava na altitude-alvo (nao ha mais motivo para
		// continuar "descendo" depois que os motores ja pararam).
		if (_gps_auto_disarm_sent) {
			land_alt_msl = target_alt_msl;
			land_vel_d = 0.0f;
		}

		_gps_land_last_alt_msl = land_alt_msl;
		_gps_land_last_alt_valid = true;

		static uint64_t land_att_hold_log_last_us = 0;

		if (!descent_attitude_ok &&
		    ((timestamp - land_att_hold_log_last_us) > AUTO_LAND_ATT_LOG_INTERVAL_US)) {
			land_att_hold_log_last_us = timestamp;
			PX4_INFO("\n[GPS-Land] Holding descent due attitude | pitch=%.1f deg | roll=%.1f deg | alt=%.2f\n",
				 land_att_recent ? static_cast<double>(ctx.sim_pitch_rad * 57.2957795f) : -999.0,
				 land_att_recent ? static_cast<double>(ctx.sim_roll_rad * 57.2957795f) : -999.0,
				 land_alt_msl);
		}

		// Sincroniza a pseudo-medicao (_jmit_pub_*), o filtro (_kf_*) e o
		// dead-reckoning do blackout (_blackout_dr_*) com a mesma posicao
		// de pouso - garante que, independente de qual caminho de codigo
		// consultar o estado depois, todos vejam a mesma realidade.
		_jmit_pub_n_m = _gps_land_hold_n_m;
		_jmit_pub_e_m = _gps_land_hold_e_m;
		_jmit_pub_alt = land_alt_msl;
		_jmit_pub_vel_n = 0.0f;
		_jmit_pub_vel_e = 0.0f;
		_jmit_pub_vel_d = land_vel_d;

		_kf_n_m = _jmit_pub_n_m;
		_kf_e_m = _jmit_pub_e_m;
		_kf_alt = _jmit_pub_alt;
		_kf_vel_n = 0.0f;
		_kf_vel_e = 0.0f;
		_kf_vel_d = land_vel_d;

		_blackout_dr_n_m = _gps_land_hold_n_m;
		_blackout_dr_e_m = _gps_land_hold_e_m;
		_blackout_dr_alt = land_alt_msl;
		_blackout_dr_vel_n = 0.0f;
		_blackout_dr_vel_e = 0.0f;
		_blackout_dr_vel_d = land_vel_d;

		double land_lat = 0.0, land_lon = 0.0;
		pos_ref.reproject(static_cast<float>(_gps_land_hold_n_m), static_cast<float>(_gps_land_hold_e_m),
				   land_lat, land_lon);

		latitude = land_lat;
		longitude = land_lon;
		altitude = land_alt_msl;
		vel_north = 0.0f;
		vel_east = 0.0f;
		vel_down = land_vel_d;
	}
}

// Envia comando de desarme assim que o detector de pouso do PX4 confirmar contato com o solo.
void GpsMitigation::checkAutoDisarm(uint64_t timestamp, const GpsMitigationContext &ctx)
{
	// So verifica desarme se o pouso ja foi iniciado (e ainda nao desarmou).
	if (!_gps_landing_active || !_gps_auto_land_sent || _gps_auto_disarm_sent) {
		return;
	}

	// Fonte 1 (preferida): o proprio land detector do PX4, que ja combina
	// varios sinais internos (empuxo, aceleracao, etc.) para decidir se o
	// veiculo pousou. Quando disponivel e confia que pousou (`.landed`),
	// o desarme e "normal" (nao forcado).
	vehicle_land_detected_s land_detected{};
	const bool land_detector_available = _vehicle_land_detected_sub.copy(&land_detected);

	const bool land_detector_recent =
		land_detector_available &&
		(land_detected.timestamp > 0) &&
		(timestamp >= land_detected.timestamp) &&
		((timestamp - land_detected.timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US);

	const bool ground_contact_by_px4_landed = land_detector_recent && land_detected.landed;

	// Sinal mais fraco do mesmo detector: "contato" ou "talvez pousado" -
	// ainda nao e certeza total, mas ja e o suficiente para autorizar um
	// desarme de seguranca (forcado) se as demais condicoes baterem.
	const bool ground_contact_by_px4_early =
		land_detector_recent && (land_detected.ground_contact || land_detected.maybe_landed);

	// Fonte 2: sensor de distancia fisico para baixo (down-facing lidar),
	// caso o modelo tenha um - confirma contato pela leitura de proximidade.
	const bool ground_sensor_recent =
		ctx.ground_distance_valid &&
		((timestamp - ctx.ground_distance_timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US);

	const bool ground_contact_by_sensor =
		ground_sensor_recent &&
		PX4_ISFINITE(ctx.ground_distance_m) &&
		(ctx.ground_distance_m <= AUTO_LAND_GROUND_DISARM_DIST_M);

	// Fonte 3 (ultimo recurso): altura acima do solo do proprio simulador
	// (ground truth), usada so quando as duas fontes acima nao estao
	// disponiveis. Exige varias amostras consecutivas abaixo do limiar
	// (SIM_AGL_GROUND_CONFIRM_COUNT) antes de contar como contato -
	// protege contra uma unica leitura ruidosa autorizando o desarme cedo
	// demais, ja que esta fonte nao tem a mesma robustez das outras duas.
	const bool sim_ground_recent =
		ctx.sim_agl_valid &&
		(timestamp >= ctx.sim_agl_timestamp) &&
		((timestamp - ctx.sim_agl_timestamp) <= AUTO_LAND_GROUND_SENSOR_TIMEOUT_US) &&
		PX4_ISFINITE(ctx.sim_agl_m);

	if (sim_ground_recent) {
		if (ctx.sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) {
			_sim_agl_ground_count++;
		} else {
			_sim_agl_ground_count = 0;
		}
	} else {
		_sim_agl_ground_count = 0;
	}

	const bool ground_contact_by_sim_agl =
		sim_ground_recent &&
		(ctx.sim_agl_m <= AUTO_LAND_SIM_GROUND_DISARM_AGL_M) &&
		(_sim_agl_ground_count >= SIM_AGL_GROUND_CONFIRM_COUNT);

	// Precisa de PELO MENOS uma das quatro fontes confirmando contato -
	// nunca desarma so por tempo decorrido desde o inicio do pouso.
	if (!ground_contact_by_px4_landed &&
	    !ground_contact_by_px4_early &&
	    !ground_contact_by_sensor &&
	    !ground_contact_by_sim_agl) {
		return;
	}

	// So e "normal" (nao forcado) quando o proprio land detector do PX4 ja
	// confirma pouso oficial; qualquer outra fonte implica em forcar o
	// desarme (ver o codigo especial 21196.0 em publishDisarmCommand()).
	const bool force_disarm = !ground_contact_by_px4_landed;

	const bool disarm_att_recent =
		ctx.sim_att_valid &&
		(timestamp >= ctx.sim_att_timestamp) &&
		((timestamp - ctx.sim_att_timestamp) <= 500000ULL);

	const double disarm_roll_deg = disarm_att_recent ? static_cast<double>(ctx.sim_roll_rad * 57.2957795f) : -999.0;
	const double disarm_pitch_deg = disarm_att_recent ? static_cast<double>(ctx.sim_pitch_rad * 57.2957795f) : -999.0;

	const float disarm_jmit_gs = sqrtf(_jmit_pub_vel_n * _jmit_pub_vel_n + _jmit_pub_vel_e * _jmit_pub_vel_e);

	// Antes de um desarme FORCADO (fontes menos confiaveis que o land
	// detector nativo), exige velocidade proxima de zero e inclinacao
	// dentro do limite normal - evita desarmar em pleno voo por engano
	// caso alguma das fontes auxiliares tenha disparado precocemente.
	const bool forced_disarm_speed_ok =
		(ctx.lpos_ground_speed <= 0.15f) &&
		(disarm_jmit_gs <= 0.10f);

	const bool forced_disarm_att_ok =
		!disarm_att_recent ||
		((fabsf(ctx.sim_roll_rad) <= AUTO_LAND_MAX_ROLL_RAD) &&
		 (fabsf(ctx.sim_pitch_rad) <= AUTO_LAND_MAX_PITCH_RAD));

	if (force_disarm && (!forced_disarm_speed_ok || !forced_disarm_att_ok)) {
		static uint64_t disarm_wait_log_last_us = 0;

		if ((timestamp - disarm_wait_log_last_us) > AUTO_LAND_ATT_LOG_INTERVAL_US) {
			disarm_wait_log_last_us = timestamp;
			PX4_INFO("\n[GPS-Land] Waiting before forced disarm | agl=%.2f | dist=%.2f | lpos_gs=%.2f | jmit_gs=%.2f | roll=%.1f pitch=%.1f\n",
				 sim_ground_recent ? static_cast<double>(ctx.sim_agl_m) : -1.0,
				 ground_sensor_recent ? static_cast<double>(ctx.ground_distance_m) : -1.0,
				 static_cast<double>(ctx.lpos_ground_speed),
				 static_cast<double>(disarm_jmit_gs),
				 disarm_roll_deg,
				 disarm_pitch_deg);
		}

		return;
	}

	// Log identificando qual das quatro fontes confirmou o contato -
	// util para diagnostico posterior de por que o desarme aconteceu.
	if (ground_contact_by_px4_landed) {
		PX4_WARN("\n[GPS-Land] PX4 landing detector confirmed landed | roll=%.1f pitch=%.1f - normal disarm\n",
			 disarm_roll_deg, disarm_pitch_deg);
	} else if (ground_contact_by_px4_early) {
		PX4_WARN("\n[GPS-Land] PX4 landing detector reported ground contact | roll=%.1f pitch=%.1f - safety disarm\n",
			 disarm_roll_deg, disarm_pitch_deg);
	} else if (ground_contact_by_sensor) {
		PX4_WARN("\n[GPS-Land] Downward distance sensor reported ground contact | dist=%.2f m | roll=%.1f pitch=%.1f - safety disarm\n",
			 static_cast<double>(ctx.ground_distance_m), disarm_roll_deg, disarm_pitch_deg);
	} else {
		PX4_WARN("\n[GPS-Land] Simulated ground proximity reached | agl=%.2f m | roll=%.1f pitch=%.1f - safety disarm\n",
			 static_cast<double>(ctx.sim_agl_m), disarm_roll_deg, disarm_pitch_deg);
	}

	publishDisarmCommand(timestamp, force_disarm);
	_gps_auto_disarm_sent = true;
	_gps_auto_land_sent = true;
	_gps_landing_active = false;
}

// Envia LAND com coordenada global explicita no ponto de chegada.
void GpsMitigation::publishLandCommand(uint64_t timestamp, MapProjection &pos_ref)
{
	// Converte a posicao de pouso (guardada em coordenadas locais N/E,
	// metros) de volta para latitude/longitude - o VEHICLE_CMD_NAV_LAND
	// exige coordenada global, nao local.
	double land_lat = 0.0, land_lon = 0.0;
	pos_ref.reproject(static_cast<float>(_gps_land_hold_n_m), static_cast<float>(_gps_land_hold_e_m),
			   land_lat, land_lon);

	vehicle_command_s cmd{};
	cmd.timestamp = timestamp;
	cmd.param1 = 0.0f;
	cmd.param2 = 0.0f;
	cmd.param3 = 0.0f;
	cmd.param4 = NAN; // heading livre (sem yaw forcado no comando)

	// Os tres ramos preenchem param5/param6 (lat/lon) da mesma forma; so o
	// param7 (altitude alvo) muda: no blackout, o comando ja leva a
	// altitude-alvo explicita (nao ha GPS real para o controlador de pouso
	// nativo do PX4 usar como referencia). Nos outros dois casos, a
	// descida em si e feita por applyLandingOverride() ciclo a ciclo -
	// aqui so fixa a posicao horizontal e deixa o param7 livre (NAN).
	if (_gps_land_source_blackout) {
		cmd.param5 = static_cast<float>(land_lat);
		cmd.param6 = static_cast<float>(land_lon);
		cmd.param7 = static_cast<float>(_gps_land_target_alt_msl);
	} else if (_gps_land_source_severe_anomaly) {
		cmd.param5 = static_cast<float>(land_lat);
		cmd.param6 = static_cast<float>(land_lon);
		cmd.param7 = NAN;
	} else {
		cmd.param5 = static_cast<float>(land_lat);
		cmd.param6 = static_cast<float>(land_lon);
		cmd.param7 = NAN;
	}

	cmd.command = vehicle_command_s::VEHICLE_CMD_NAV_LAND;
	cmd.target_system = 1;
	cmd.target_component = 1;
	cmd.source_system = 1;
	cmd.source_component = 1;
	cmd.confirmation = 0;
	cmd.from_external = false;

	_vehicle_command_pub.publish(cmd);

	PX4_WARN("\n[GPS-Land] Controlled landing started | src=%s | hold N=%.1f E=%.1f | alt %.1f -> %s | lat=%.7f lon=%.7f\n",
		 _gps_land_source_blackout ? "blackout" : "anomaly",
		 _gps_land_hold_n_m, _gps_land_hold_e_m, _gps_land_start_alt_msl,
		 _gps_land_source_blackout ? "target" : "fixed-pos",
		 land_lat, land_lon);
}

// Envia o comando de desarme do PX4 apos confirmacao de contato com o solo.
void GpsMitigation::publishDisarmCommand(uint64_t timestamp, bool force_disarm)
{
	vehicle_command_s cmd{};
	cmd.timestamp = timestamp;
	cmd.param1 = 0.0f;
	// 21196.0 e o "magic number" que o PX4 exige em param2 para aceitar um
	// desarme forcado (fora das condicoes normais de desarme em solo) -
	// usado quando a confirmacao veio de sensor de distancia/AGL simulado
	// em vez do land detector nativo do PX4 (ver checkAutoDisarm()).
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

	PX4_WARN("\n[GPS-Land] %s disarm command sent after ground contact confirmation.\n",
		 force_disarm ? "Forced" : "Normal");
}
// ======== MITIGACAO DE POUSO GPS - FIM ========