/****************************************************************************
 *
 *   UAVJamSim - Stream Mitigation Module (implementation)
 * 
 *   Roda no processo do Gazebo (GstCameraSystem), nao no processo do PX4
 *   como as demais mitigacoes - por isso nao tem acesso a uORB. Mantem seu
 *   proprio no gz-transport: assina o topico unico de liga/desliga
 *   (/gazebo/default/mitigation/control, o mesmo que todas as mitigacoes do
 *   lado do PX4 respeitam) e publica um sinal em topico proprio quando cada
 *   anomalia e confirmada, para o GZBridge (que roda no processo do PX4, com
 *   acesso ao terminal via PX4_WARN) avisar o operador.
 *
 *   Flip: deteccao por continuidade temporal - compara o frame atual contra
 *   o ultimo frame bom guardado, nas duas formas (como esta, e invertido
 *   verticalmente). So um flip de verdade produz uma divergencia grande que
 *   desaparece ao desfazer a inversao; movimento normal de camera nunca
 *   gera essa assinatura especifica.
 *
 *   Quadrado preto: deteccao radial a partir do centro do frame, usando o
 *   conhecimento parcial de que o ataque sempre fica centralizado (sem
 *   assumir tamanho exato) - anda em 8 direcoes a partir do pixel central
 *   contando quantos pixels quase-pretos consecutivos existem antes de
 *   bater em algo que nao e escuro.
 *
 ****************************************************************************/

#include "StreamMitigation.hpp"

#include <algorithm>

#include <gz/common/Console.hh>

bool StreamMitigation::init()
{
	_node.Subscribe("/gazebo/default/mitigation/control", &StreamMitigation::onMitigationCmd, this);
	_flipDetectedPub = _node.Advertise<gz::msgs::Int32>("/gazebo/default/attack/stream_flip_detected");
	_blackDetectedPub = _node.Advertise<gz::msgs::Int32>("/gazebo/default/attack/stream_black_detected");
	return true;
}

// ======== MITIGACAO GERAL - INICIO ========
void StreamMitigation::onMitigationCmd(const gz::msgs::Vector3d &msg)
{
	const bool enabled = (static_cast<int>(msg.x()) == 1);
	_mitigationEnabled.store(enabled, std::memory_order_relaxed);
	gzdbg << "StreamMitigation: mitigation " << (enabled ? "ENABLED" : "DISABLED") << std::endl;
}
// ======== MITIGACAO GERAL - FIM ========

// ======== MITIGACAO FLIP - INICIO ========
// Deteccao por continuidade temporal, sem consultar a opcao de ataque:
// compara o frame publicado (ja com o ataque aplicado, se houver) contra o
// ultimo frame bom guardado, nas duas formas — como esta, e invertido
// verticalmente. So um flip de verdade produz uma divergencia grande que
// desaparece ao desfazer a inversao; movimento normal de camera nunca gera
// essa assinatura especifica.
void StreamMitigation::detectAndCorrectFlip(cv::Mat &frame)
{
	if (!_mitigationEnabled.load(std::memory_order_relaxed)) {
		return;
	}

	cv::Mat small_gray;
	cv::cvtColor(frame, small_gray, cv::COLOR_RGB2GRAY);
	cv::resize(small_gray, small_gray, cv::Size(FLIP_DETECT_WIDTH, FLIP_DETECT_HEIGHT));

	bool sample_anomalous = false;

	if (!_lastGoodFrameGray.empty()) {
		cv::Mat flipped_small;
		cv::flip(small_gray, flipped_small, 0);

		const double pixel_count = static_cast<double>(FLIP_DETECT_WIDTH * FLIP_DETECT_HEIGHT);
		const double diff_normal =
			cv::norm(small_gray, _lastGoodFrameGray, cv::NORM_L1) / pixel_count;
		const double diff_flipped =
			cv::norm(flipped_small, _lastGoodFrameGray, cv::NORM_L1) / pixel_count;

		sample_anomalous =
			(diff_normal > FLIP_MIN_ABS_DIFF) &&
			(diff_flipped < diff_normal * FLIP_MATCH_RATIO);
	}

	if (sample_anomalous) {
		_flipRecoveryStreak = 0;
		_flipAnomalyStreak++;

		if (!_flipAnomalyActive && (_flipAnomalyStreak >= FLIP_CONFIRM_FRAMES)) {
			_flipAnomalyActive = true;
			gzwarn << "Attack detected: FLIP" << std::endl;

			gz::msgs::Int32 flip_signal;
			flip_signal.set_data(1);
			_flipDetectedPub.Publish(flip_signal);
		}

	} else {
		_flipAnomalyStreak = 0;

		if (_flipAnomalyActive) {
			_flipRecoveryStreak++;

			if (_flipRecoveryStreak >= FLIP_RECOVERY_FRAMES) {
				_flipAnomalyActive = false;
				gzdbg << "StreamMitigation: flip anomaly cleared" << std::endl;
			}
		}
	}

	// Corrige desfazendo a inversao (operacao auto-reversivel).
	if (_flipAnomalyActive) {
		cv::flip(frame, frame, 0);
	}

	// Atualiza a referencia em dois casos, nunca com o sinal atacado cru:
	//  (a) amostra nao-anomala: usa o frame como esta.
	//  (b) ataque ja confirmado e sendo corrigido: usa a versao corrigida
	//      (invertida de volta), para a referencia continuar acompanhando
	//      a cena real conforme o drone se move, em vez de ficar congelada
	//      indefinidamente — uma referencia parada demais durante um
	//      ataque longo com bastante movimento podia divergir o
	//      suficiente da cena atual para soltar uma recuperacao falsa no
	//      meio do ataque.
	// So durante a janela de confirmacao (sintoma presente, ainda nao
	// confirmado) o cache fica sem atualizar: atualizar aqui contaminaria
	// a referencia com o proprio frame invertido em 1-2 frames, fazendo o
	// sintoma "desaparecer" antes de completar a confirmacao — mesmo
	// principio de linha de base usado nas demais mitigacoes (so evolui
	// com amostra saudavel ou ja corrigida).
	if (!sample_anomalous) {
		_lastGoodFrameGray = small_gray.clone();

	} else if (_flipAnomalyActive) {
		cv::Mat corrected_small;
		cv::flip(small_gray, corrected_small, 0);
		_lastGoodFrameGray = corrected_small;
	}
}
// ======== MITIGACAO FLIP - FIM ========

// ======== MITIGACAO QUADRADO PRETO - INICIO ========
// Deteccao radial a partir do centro do frame, sem consultar a opcao de
// ataque — a partir do pixel central, anda em 8 direcoes (cima, baixo,
// esquerda, direita, 4 diagonais) contando quantos pixels quase-pretos
// consecutivos existem antes de bater em algo que nao e escuro. So conta
// como sintoma se o centro for escuro e a extensao escura alcancar um raio
// minimo em praticamente todas as direcoes ao mesmo tempo — um bloco
// solido de ataque se expande de forma uniforme a partir do centro; uma
// arvore, mesmo escura, quase sempre tem contorno irregular (galhos,
// aberturas entre folhas) que quebra a continuidade em pelo menos algumas
// direcoes antes de alcancar esse raio, mesmo quando calha de estar bem no
// centro do quadro.
void StreamMitigation::detectAndCorrectBlackSquare(cv::Mat &frame)
{
	if (!_mitigationEnabled.load(std::memory_order_relaxed)) {
		return;
	}

	const int width = frame.cols;
	const int height = frame.rows;

	cv::Mat gray;
	cv::cvtColor(frame, gray, cv::COLOR_RGB2GRAY);

	const int cx = width / 2;
	const int cy = height / 2;

	bool sample_anomalous = false;
	cv::Rect region_bounds(cx, cy, 0, 0);

	if (gray.at<uchar>(cy, cx) < BLACK_PIXEL_THRESHOLD) {

		static const int dirX[8] = {1, -1, 0, 0, 1, 1, -1, -1};
		static const int dirY[8] = {0, 0, 1, -1, 1, -1, 1, -1};

		int directionsOk = 0;
		int minX = cx, maxX = cx, minY = cy, maxY = cy;

		for (int d = 0; d < 8; d++) {
			int radius = 0;
			int x = cx;
			int y = cy;

			// Anda ate a borda real da regiao escura (ou o limite do
			// frame), nao so ate o raio minimo de deteccao — o raio
			// minimo decide se e ataque, mas o retangulo de correcao
			// precisa cobrir a extensao verdadeira, senao sobra um anel
			// sem corrigir entre o raio minimo e a borda real do
			// quadrado.
			while (true) {
				const int nx = x + dirX[d];
				const int ny = y + dirY[d];

				if ((nx < 0) || (nx >= width) || (ny < 0) || (ny >= height)) {
					break;
				}

				if (gray.at<uchar>(ny, nx) >= BLACK_PIXEL_THRESHOLD) {
					break;
				}

				x = nx;
				y = ny;
				radius++;
			}

			if (radius >= BLACK_MIN_RADIUS_PX) {
				directionsOk++;
			}

			minX = std::min(minX, x);
			maxX = std::max(maxX, x);
			minY = std::min(minY, y);
			maxY = std::max(maxY, y);
		}

		sample_anomalous = directionsOk >= BLACK_MIN_DIRECTIONS_OK;

		if (sample_anomalous) {
			region_bounds = cv::Rect(
						minX, minY, (maxX - minX) + 1, (maxY - minY) + 1);
		}
	}

	cv::Mat region_mask;

	if (sample_anomalous) {
		region_mask = cv::Mat::zeros(height, width, CV_8UC1);
		region_mask(region_bounds).setTo(255);
	}

	if (sample_anomalous) {
		_blackRecoveryStreak = 0;
		_blackAnomalyStreak++;

		if (!_blackAnomalyActive && (_blackAnomalyStreak >= BLACK_CONFIRM_FRAMES)) {
			_blackAnomalyActive = true;
			gzwarn << "Attack detected: BLACK_SQUARE" << std::endl;

			gz::msgs::Int32 black_signal;
			black_signal.set_data(1);
			_blackDetectedPub.Publish(black_signal);
		}

	} else {
		_blackAnomalyStreak = 0;

		if (_blackAnomalyActive) {
			_blackRecoveryStreak++;

			if (_blackRecoveryStreak >= BLACK_RECOVERY_FRAMES) {
				_blackAnomalyActive = false;
				gzdbg << "StreamMitigation: black square anomaly cleared" << std::endl;
			}
		}
	}

	// Reconstroi a regiao por inpainting espacial, so olhando o proprio
	// frame atual — nao depende de frame anterior nenhum, entao nao ha
	// risco de desalinhamento com o drone em movimento.
	if (_blackAnomalyActive && sample_anomalous && !region_mask.empty()) {
		cv::Mat inpainted;
		cv::inpaint(frame, region_mask, inpainted, 5, cv::INPAINT_TELEA);
		frame = inpainted;
	}
}
// ======== MITIGACAO QUADRADO PRETO - FIM ========