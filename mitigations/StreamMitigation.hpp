/****************************************************************************
 *
 *   UAVJamSim - Stream Mitigation Module (flip / quadrado preto)
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

#pragma once

#include <atomic>

#include <gz/msgs.hh>
#include <gz/transport.hh>

#include <opencv2/opencv.hpp>

class StreamMitigation
{
public:
	explicit StreamMitigation(gz::transport::Node &node) : _node(node) {}

	// Assina o topico unico de liga/desliga e anuncia os dois topicos de
	// sinal de anomalia confirmada.
	bool init();

	// Chamados de GstCameraSystem::onImage, apos o ataque ser aplicado,
	// nesta ordem (independentes entre si, cada um so mexe no que detecta).
	// Corrigem frame em-lugar (in-place) quando a anomalia esta confirmada.
	void detectAndCorrectFlip(cv::Mat &frame);
	void detectAndCorrectBlackSquare(cv::Mat &frame);

private:
	void onMitigationCmd(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;
	std::atomic<bool> _mitigationEnabled{false};

	// ======== Estado da deteccao de flip ========
	cv::Mat _lastGoodFrameGray;
	int _flipAnomalyStreak{0};
	int _flipRecoveryStreak{0};
	bool _flipAnomalyActive{false};
	gz::transport::Node::Publisher _flipDetectedPub;
	static constexpr int FLIP_DETECT_WIDTH = 160;
	static constexpr int FLIP_DETECT_HEIGHT = 90;
	static constexpr int FLIP_CONFIRM_FRAMES = 3;
	static constexpr int FLIP_RECOVERY_FRAMES = 3;
	// Diferenca minima absoluta (em niveis de cinza, 0-255) para considerar
	// o quadro atual "genuinamente diferente" do ultimo bom; abaixo disso,
	// nao ha dado suficiente para decidir (cena praticamente parada).
	static constexpr double FLIP_MIN_ABS_DIFF = 8.0;
	// A comparacao invertida precisa bater pelo menos essa fracao melhor
	// que a comparacao normal para contar como sintoma.
	static constexpr double FLIP_MATCH_RATIO = 0.6;

	// ======== Estado da deteccao de quadrado preto ========
	int _blackAnomalyStreak{0};
	int _blackRecoveryStreak{0};
	bool _blackAnomalyActive{false};
	static constexpr int BLACK_CONFIRM_FRAMES = 5;
	static constexpr int BLACK_RECOVERY_FRAMES = 3;
	// Pixels de cinza abaixo deste valor (0-255) sao considerados escuros.
	static constexpr int BLACK_PIXEL_THRESHOLD = 15;
	// Raio minimo (pixels) que a extensao escura precisa alcancar em cada
	// direcao a partir do centro. O ataque real usa lado igual a
	// min(largura,altura)/6 - em 1920x1080 isso da 180px de lado, ou seja,
	// so 90px do centro ate a borda em qualquer direcao. O valor abaixo
	// fica com folga confortavel abaixo desse maximo fisico.
	static constexpr int BLACK_MIN_RADIUS_PX = 60;
	// Das 8 direcoes testadas, quantas precisam alcancar o raio minimo
	// para confirmar o sintoma. Nao exige as 8 (tolera alguma quebra de
	// continuidade nas bordas).
	static constexpr int BLACK_MIN_DIRECTIONS_OK = 7;
	gz::transport::Node::Publisher _blackDetectedPub;
};