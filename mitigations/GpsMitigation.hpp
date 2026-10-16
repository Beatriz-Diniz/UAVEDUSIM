/****************************************************************************
 *
 *   UAVJamSim - GPS Mitigation Module
 *
 *   Cobre os ataques de jamming, offset absoluto e rotacao (todos os tres
 *   sao apenas perturbacoes na medicao de GPS antes desta mitigacao rodar -
 *   ela nao distingue qual ataque gerou a anomalia, so reage ao sintoma).
 *
 *   ESTRATEGIAS IMPLEMENTADAS
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
 *
 ****************************************************************************/

#pragma once

#include <cstdint>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/actuator_armed.h>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/sensor_gps.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

// ======== GpsMitigationContext ========
// Estrutura pura de dados (sem logica), preenchida pelo GZBridge a cada
// ciclo a partir dos caches que outros callbacks (odometryCallback,
// imuCallback/LPOS, poseInfoCallback, sensor de distancia, barometro)
// ja mantinham antes desta extracao. Esses caches sao compartilhados
// com as mitigacoes de outros sensores (IMU, barometro, lidar) - por
// isso continuam populados pelo GZBridge, e apenas repassados por
// referencia para GpsMitigation em vez de serem duplicados aqui.
struct GpsMitigationContext {
	// VIO (odometria visual), escrito em GZBridge::odometryCallback.
	bool vio_valid{false};
	uint64_t vio_timestamp{0};
	double vio_n_m{0.0};
	double vio_e_m{0.0};
	double vio_alt_msl{0.0};
	float vio_vel_n{0.0f};
	float vio_vel_e{0.0f};
	float vio_vel_d{0.0f};

	// Estimativa local do PX4 (LPOS), escrita em GZBridge::imuCallback.
	// Compartilhada com o pouso generico de IMU/motor/mag/lidar/baro.
	bool lpos_xy_valid{false};
	bool lpos_z_valid{false};
	uint64_t lpos_timestamp{0};
	double lpos_n_m{0.0};
	double lpos_e_m{0.0};
	double lpos_alt_msl{0.0};
	float imu_dr_vel_n{0.0f};
	float imu_dr_vel_e{0.0f};
	float imu_dr_vel_d{0.0f};
	float lpos_ground_speed{0.0f};
	float lpos_heading_rad{0.0f};

	// Atitude/altura acima do solo (ground truth), escrita em GZBridge::poseInfoCallback.
	bool sim_att_valid{false};
	uint64_t sim_att_timestamp{0};
	float sim_roll_rad{0.0f};
	float sim_pitch_rad{0.0f};
	bool sim_agl_valid{false};
	uint64_t sim_agl_timestamp{0};
	double sim_agl_m{0.0};

	// Sensor de distancia para baixo, escrito no callback do LiDAR down.
	bool ground_distance_valid{false};
	uint64_t ground_distance_timestamp{0};
	float ground_distance_m{0.0f};

	// Cache do barometro (leitura E escrita: GpsMitigation calibra o offset
	// aqui quando a amostra de GPS e nominal; o barometro le esse offset
	// como referencia independente).
	bool jmit_baro_valid{false};
	uint64_t jmit_baro_timestamp{0};
	float jmit_baro_alt_m{0.0f};
	bool jmit_baro_alt_offset_valid{false};
	double jmit_baro_alt_offset{0.0};

	// Cache de altitude do LPOS com timestamp proprio, usado no calculo de
	// trusted_alt na inicializacao do filtro.
	bool jmit_lpos_alt_valid{false};
	uint64_t jmit_lpos_timestamp{0};
	double jmit_lpos_alt_msl{0.0};
};

class GpsMitigation
{
public:
	// Reinicia todo o estado proprio (KF, blackout, pouso). Chamado pelo
	// GZBridge quando a mitigacao geral e ligada/desligada.
	void reset();


	// Chamado de GZBridge::navSatCallback a cada amostra de GPS.
	//
	// ESTRATEGIA (filtro de Kalman + NIS): prediz onde o drone deveria
	// estar com base na velocidade estimada do ciclo anterior, compara
	// contra a medicao recebida, e normaliza a diferenca pela incerteza
	// atual do filtro (NIS). Um NIS acima do limiar estatistico
	// (JMIT_NIS_THRESHOLD) indica que a medicao e incompativel com o
	// historico recente - candidato a ataque, seja ele offset constante,
	// rotacao ou ruido de jamming (o metodo nao precisa saber qual).
	//
	// Se a mitigacao estiver ligada e a amostra for anomala, a funcao
	// REJEITA a medicao e substitui latitude/longitude/altitude/velocidade
	// pela pseudo-medicao de dead-reckoning (VIO > LPOS > ultimo valor
	// mantido), a mesma estrategia usada no blackout. Se a amostra for
	// nominal, o filtro e atualizado normalmente (ganho de Kalman) e,
	// nesse ramo, tambem calibra ctx.jmit_baro_alt_offset - a referencia
	// cruzada que a mitigacao de barometro usa para detectar ataques de
	// pressao (ver cabecalho do arquivo, estrategia 3).
	//
	// Retorna true se a amostra foi considerada anomala neste ciclo.
	bool detectAndCorrect(bool mitigation_enabled, MapProjection &pos_ref, GpsMitigationContext &ctx,
			      double &latitude, double &longitude, double &altitude,
			      float &vel_north, float &vel_east, float &vel_down);

	// Chamado de GZBridge::imuCallback a cada amostra de IMU (roda mais
	// frequente que o proprio GPS, entao o dead-reckoning nao fica parado
	// entre uma amostra de GPS e outra).
	//
	// ESTRATEGIA (pseudo-medicao por prioridade de fonte): quando o GPS
	// real para de publicar por tempo demais (BLACKOUT_GPS_TIMEOUT_US),
	// a funcao mantem uma estimativa de posicao propria, escolhendo a
	// melhor fonte disponivel nesta ordem:
	//   1. VIO (odometria visual) - independente do GPS, a mais confiavel;
	//   2. LPOS (estimativa local do proprio PX4) - se VIO nao esta recente;
	//   3. Extrapolacao pela ultima velocidade conhecida, com decaimento -
	//      usada so quando nem VIO nem LPOS estao disponiveis, para nao
	//      deixar a posicao "congelada" indefinidamente.
	// Preenche out_pseudo_gps (throttled a BLACKOUT_PUB_INTERVAL_US, ja
	// que o dead-reckoning roda mais rapido que a publicacao precisa ser)
	// quando e hora de publicar. Retorna true quando out_pseudo_gps deve
	// ser publicada pelo chamador.
	bool tickBlackout(bool mitigation_enabled, uint64_t timestamp, MapProjection &pos_ref,
			   const GpsMitigationContext &ctx,
			   bool gps_real_valid, uint64_t gps_real_last_us,
			   double gps_real_n_m, double gps_real_e_m, double gps_real_alt,
			   float gps_real_vel_n, float gps_real_vel_e,
			   int nominal_satellites,
			   sensor_gps_s &out_pseudo_gps);

	// Os tres metodos abaixo sao chamados de GZBridge::navSatCallback,
	// nesta ordem exata (mesma ordem do codigo original):
	//   checkAutoLand() -> applyLandingOverride() -> checkAutoDisarm()
	//
	// ESTRATEGIA (pouso guiado pela pseudo-medicao): usa a mesma posicao
	// estimada de detectAndCorrect()/tickBlackout() - nao o GPS real, que
	// pode estar comprometido - para verificar se o drone ja chegou perto
	// o suficiente do destino da missao. So aciona o pouso apos permanecer
	// dentro do raio de chegada por uma janela minima sustentada (evita
	// disparo por uma unica amostra de coincidencia), com criterios mais
	// permissivos para blackout (situacao mais grave) do que para ruido.
	void checkAutoLand(bool mitigation_enabled, uint64_t timestamp, MapProjection &pos_ref, float alt_ref,
			    const GpsMitigationContext &ctx, double gps_real_alt);

	// Sobrescreve latitude/longitude/altitude/velocidade com a posicao de
	// pouso enquanto o pouso esta em andamento (ou a espera de confirmacao
	// antes dele comecar oficialmente) - garante que o sensor_gps publicado
	// mostre o drone descendo suavemente na posicao-alvo, em vez de saltar
	// para onde o GPS (possivelmente atacado) diz que ele esta.
	void applyLandingOverride(uint64_t timestamp, MapProjection &pos_ref, float alt_ref,
				   const GpsMitigationContext &ctx,
				   double &latitude, double &longitude, double &altitude,
				   float &vel_north, float &vel_east, float &vel_down,
				   bool &gps_anomalous_now);

	// ESTRATEGIA (confirmacao por multiplas fontes de contato com o solo):
	// so desarma quando pelo menos uma entre land detector do PX4, sensor
	// de distancia para baixo ou altura simulada confirma contato com o
	// solo - nunca so por tempo decorrido, para nao desarmar no ar por
	// engano caso a estimativa de altitude esteja levemente errada.
	void checkAutoDisarm(uint64_t timestamp, const GpsMitigationContext &ctx);

	// ======== Estado / consulta ========
	bool isLandingActive() const { return _gps_landing_active; }
	bool isAutoLandSent() const { return _gps_auto_land_sent; }
	bool isAutoDisarmSent() const { return _gps_auto_disarm_sent; }
	bool isLandingHeadingValid() const { return _gps_land_heading_valid; }
	float landingHeadingRad() const { return _gps_land_heading_rad; }
	bool isBlackoutAnchorInitialized() const { return _blackout_anchor_initialized; }
	bool isBlackoutDetected() const { return _blackout_detected; }
	double blackoutDrNm() const { return _blackout_dr_n_m; }
	double blackoutDrEm() const { return _blackout_dr_e_m; }
	double blackoutDrAlt() const { return _blackout_dr_alt; }
	float blackoutDrVelN() const { return _blackout_dr_vel_n; }
	float blackoutDrVelE() const { return _blackout_dr_vel_e; }
	float blackoutDrVelD() const { return _blackout_dr_vel_d; }

	// true quando a pseudo-medicao (ancora KF, blackout ou pouso) esta
	// sendo publicada no lugar da medicao real.
	bool isAnchorNow(bool mitigation_enabled) const
	{
		return mitigation_enabled && ((_kf_attack_dur_us > 0) || _gps_landing_active || _gps_auto_disarm_sent);
	}

	bool isLandingNow() const { return _gps_landing_active || _gps_auto_disarm_sent; }

private:
	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm);

	// ======== Estado do filtro (KF simples de posicao/altitude/velocidade) ========
	// Estimativa atual do filtro: o que a mitigacao acredita ser a posicao/
	// altitude/velocidade real do drone, independente do que o GPS acabou
	// de reportar. Em condicoes normais, isso converge para perto do GPS
	// real (o filtro "confia" na medicao). Durante um ataque, essas
	// variaveis passam a ser realimentadas pela pseudo-medicao (VIO/LPOS/
	// extrapolacao) em vez do GPS, e sao elas - nao o GPS bruto - que
	// acabam saindo publicadas.
	double _kf_n_m{0.0};
	double _kf_e_m{0.0};
	double _kf_alt{0.0};
	float  _kf_vel_n{0.0f};
	float  _kf_vel_e{0.0f};
	float  _kf_vel_d{0.0f};

	// Covariancias (incerteza) do filtro para cada eixo. Comecam baixas
	// (confianca alta) e crescem a cada ciclo pela injecao de ruido de
	// processo (Q); toda vez que uma medicao nominal e aceita, o ganho de
	// Kalman as reduz de volta. Sao essas covariancias que definem o quao
	// "surpreendente" uma nova medicao precisa ser para disparar o NIS alto.
	double _kf_P_n{1.0};
	double _kf_P_e{1.0};
	double _kf_P_alt{4.0};
	float  _kf_P_vn{0.1f};
	float  _kf_P_ve{0.1f};
	float  _kf_P_vd{0.1f};

	bool     _kf_initialized{false};
	uint64_t _kf_last_us{0};
	// Ha quanto tempo (em us) a mitigacao esta rejeitando medicoes de GPS
	// consecutivas. Usado tanto para decidir a severidade do log quanto
	// como um dos criterios de acionamento do pouso automatico (um ataque
	// breve nao aciona pouso; um ataque sustentado, sim).
	uint64_t _kf_attack_dur_us{0};
	// true apenas na primeira amostra anomala de uma sequencia - marca o
	// instante em que a pseudo-medicao precisa ser inicializada a partir
	// da melhor fonte disponivel (ver logica de prioridade VIO>LPOS>hold).
	bool     _kf_attack_first{true};

	// Pseudo-medicao GPS publicada durante a mitigacao: e este N/E/alt/vel
	// - nao a leitura crua do GPS - que sai em sensor_gps quando is_anchor_now
	// esta ativo (detectAndCorrect anomalo, ou pouso). Reaproveitada tambem
	// por checkAutoLand como estimativa de posicao para calcular a distancia
	// ate o destino da missao quando o ataque e por ruido (nao blackout).
	double _jmit_pub_n_m{0.0};
	double _jmit_pub_e_m{0.0};
	double _jmit_pub_alt{0.0};
	float  _jmit_pub_vel_n{0.0f};
	float  _jmit_pub_vel_e{0.0f};
	float  _jmit_pub_vel_d{0.0f};

	// ======== Estado do blackout (dead-reckoning) ========
	// true assim que o blackout e detectado pela primeira vez (timeout sem
	// publicacao real de GPS) - permanece assim ate o GPS real voltar.
	bool _blackout_anchor_initialized{false};

	// Ancora: posicao/altitude capturadas no exato momento em que o
	// blackout comeca, usada como ponto de partida para a extrapolacao por
	// velocidade quando nem VIO nem LPOS estao disponiveis.
	double _blackout_anchor_n_m{0.0};
	double _blackout_anchor_e_m{0.0};
	double _blackout_anchor_alt{0.0};

	// Estimativa corrente de posicao/altitude durante o blackout - atualizada
	// a cada ciclo por dead-reckoning, priorizando VIO > LPOS > extrapolacao
	// da ultima velocidade conhecida (ver tickBlackout() para a logica).
	double _blackout_dr_n_m{0.0};
	double _blackout_dr_e_m{0.0};
	double _blackout_dr_alt{0.0};

	// Velocidade usada para propagar a posicao quando nem VIO nem LPOS
	// estao disponiveis (extrapolacao pura); decai a cada ciclo para nao
	// acumular erro indefinidamente caso a velocidade real tenha mudado.
	float _blackout_dr_vel_n{0.0f};
	float _blackout_dr_vel_e{0.0f};
	float _blackout_dr_vel_d{0.0f};

	uint64_t _blackout_start_us{0};
	// Throttling da publicacao da pseudo-GPS durante blackout (10 Hz,
	// ver BLACKOUT_PUB_INTERVAL_US) - o dead-reckoning em si roda a cada
	// ciclo de IMU (mais rapido), mas a publicacao nao precisa ser tao frequente.
	uint64_t _blackout_last_pub_us{0};
	uint64_t _blackout_last_dr_us{0};
	bool     _blackout_detected{false};

	// ======== Estado de pouso/desarme ========
	bool _gps_auto_land_sent{false};
	bool _gps_auto_disarm_sent{false};
	bool _gps_landing_active{false};
	// Guarda qual foi o gatilho do pouso (blackout ou anomalia por ruido),
	// ja que os dois usam raio de chegada, velocidade maxima e altitude-alvo
	// diferentes (blackout e mais permissivo, por ser uma situacao mais
	// grave - o GPS simplesmente sumiu).
	bool _gps_land_source_blackout{false};
	bool _gps_land_source_severe_anomaly{false};

	uint64_t _gps_auto_land_arrival_us{0};
	uint64_t _gps_auto_land_last_arrived_us{0};
	uint64_t _gps_land_hold_start_us{0};

	// Posicao/altitude fixadas no momento em que o pouso e acionado -
	// o drone desce mantendo N/E constantes, so reduzindo altitude.
	double _gps_land_hold_n_m{0.0};
	double _gps_land_hold_e_m{0.0};
	double _gps_land_start_alt_msl{0.0};
	double _gps_land_target_alt_msl{0.0};
	double _gps_land_last_alt_msl{0.0};
	bool   _gps_land_last_alt_valid{false};
	uint64_t _gps_land_last_update_us{0};

	// Marca uma anomalia como "severa" (NIS muito acima do limiar normal),
	// o que reduz a janela de confirmacao do pouso - uma medicao
	// absurdamente errada nao precisa esperar tanto quanto um desvio sutil.
	bool     _gps_anomaly_severe{false};
	uint64_t _gps_anomaly_severe_us{0};

	// Heading capturado no momento da chegada ao destino, usado no comando
	// de pouso (VEHICLE_CMD_NAV_LAND) para o drone descer sem girar -
	// necessario porque com velocidade ~0 o calculo usual de heading por
	// atan2(vel_e, vel_n) fica indefinido/ruidoso.
	float _gps_land_heading_rad{0.0f};
	bool  _gps_land_heading_valid{false};

	// ======== uORB proprios (nao compartilhados com o GZBridge) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _actuator_armed_sub{ORB_ID(actuator_armed)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

	// ======== Constantes ========
	// KF: ruido de processo (Q) e de medicao (R) do filtro de posicao/velocidade/altitude.
	// Q representa o quanto o filtro "desconfia" da sua propria predicao a
	// cada ciclo (cresce a incerteza); R representa o quanto ele confia na
	// medicao de GPS quando ela e aceita como nominal. A relacao entre Q e
	// R e o que define a agressividade do ganho de Kalman - R pequeno
	// (medicao muito confiavel) faz o filtro colar na medicao; Q grande
	// faz a incerteza crescer rapido entre amostras, tornando o NIS mais
	// tolerante a pequenos desvios legitimos de manobra.
	static constexpr double JMIT_Q_POS        = 0.04;
	static constexpr double JMIT_Q_VEL        = 0.25;
	static constexpr double JMIT_Q_ALT        = 0.09;
	static constexpr double JMIT_R_POS_CLEAN  = 0.64;
	static constexpr double JMIT_R_VEL_CLEAN  = 0.0025;
	static constexpr double JMIT_R_ALT_CLEAN  = 2.56;
	// Limiar do teste estatistico de anomalia (NIS). Acima deste valor, a
	// medicao e considerada incompativel com a predicao do filtro.
	static constexpr double JMIT_NIS_THRESHOLD = 10.83;
	// Limites de velocidade aceitos no dead-reckoning (VIO ou extrapolacao)
	// - qualquer leitura acima disso e tratada como fisicamente implausivel
	// e descartada, para nao deixar um sensor auxiliar ruidoso "acelerar"
	// a pseudo-medicao para longe do real.
	static constexpr float  JMIT_DR_VEL_MAX_M_S   = 15.0f;
	static constexpr float  JMIT_DR_VEL_Z_MAX_M_S =  5.0f;
	static constexpr float  JMIT_P_MAX         = 625.0f;

	// Blackout: parametros da pseudo-medicao publicada enquanto o GPS real
	// esta ausente. O EPH/EPV/HDOP/VDOP degradados sinalizam ao restante do
	// PX4 (EKF2) que essa leitura e menos confiavel que um GPS nominal,
	// mesmo continuando com fix_type valido - o objetivo e nao mascarar a
	// degradacao, so evitar que o voo pare por falta total de posicao.
	static constexpr uint64_t BLACKOUT_MAX_HOLD_US = 300000000ULL;
	static constexpr float BLACKOUT_EPH_DEGRADE  = 3.0f;
	static constexpr float BLACKOUT_EPV_DEGRADE  = 5.0f;
	static constexpr float BLACKOUT_HDOP_DEGRADE = 1.5f;
	static constexpr float BLACKOUT_VDOP_DEGRADE = 2.5f;
	static constexpr uint64_t BLACKOUT_PUB_INTERVAL_US = 100000ULL;
	static constexpr uint64_t BLACKOUT_VIO_TIMEOUT_US = 500000ULL;
	static constexpr uint64_t BLACKOUT_GPS_TIMEOUT_US = 500000ULL;

	// Pouso/desarme automatico especifico de GPS.
	static constexpr float AUTO_LAND_DEST_RADIUS_M = 1.2f;
	static constexpr float AUTO_LAND_MAX_SPEED_M_S = 0.25f;
	static constexpr float AUTO_LAND_ANOMALY_DEST_RADIUS_M = 1.2f;
	static constexpr float AUTO_LAND_ANOMALY_MAX_SPEED_M_S = 0.45f;
	static constexpr float AUTO_LAND_SEVERE_ANOMALY_DEST_RADIUS_M = 2.0f;
	static constexpr float AUTO_LAND_SEVERE_ANOMALY_MAX_SPEED_M_S = 0.45f;
	static constexpr uint64_t AUTO_LAND_STABLE_US = 1500000ULL;
	static constexpr uint64_t AUTO_LAND_PULSE_BRIDGE_US = 6000000ULL;
	static constexpr uint64_t AUTO_LAND_SEVERE_STABLE_US = 1500000ULL;
	static constexpr float AUTO_LAND_FINAL_MAX_SPEED_M_S = 0.40f;
	static constexpr uint64_t AUTO_LAND_FINAL_MAX_WAIT_US = 8000000ULL;
	static constexpr double AUTO_LAND_SEVERE_NIS_THRESHOLD = 50000.0;
	static constexpr uint64_t AUTO_LAND_MIN_BLACKOUT_US = 500000ULL;
	static constexpr uint64_t AUTO_LAND_MIN_NOISE_US = 3000000ULL;
	static constexpr double AUTO_LAND_DESCENT_RATE_M_S = 0.08;
	static constexpr double AUTO_LAND_TARGET_AGL_M = 0.30;
	static constexpr double AUTO_LAND_TARGET_GPS_ANOMALY_AGL_M = 0.25;
	static constexpr uint64_t AUTO_LAND_GROUND_SENSOR_TIMEOUT_US = 1000000ULL;
	static constexpr float AUTO_LAND_GROUND_DISARM_DIST_M = 0.18f;
	static constexpr double AUTO_LAND_SIM_GROUND_DISARM_AGL_M = 0.12;
	static constexpr float AUTO_LAND_MAX_PITCH_RAD = 0.0872665f;
	static constexpr float AUTO_LAND_MAX_ROLL_RAD = 0.0872665f;
	static constexpr uint64_t AUTO_LAND_ATT_LOG_INTERVAL_US = 500000ULL;
	static constexpr uint32_t SIM_AGL_GROUND_CONFIRM_COUNT = 5;

	// Contador de histerese do sensor de solo simulado, usado so pelo desarme de GPS.
	uint32_t _sim_agl_ground_count{0};
};