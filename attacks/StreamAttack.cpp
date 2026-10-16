/****************************************************************************
 *
 *   UAVJamSim - Camera Stream Attack Module (implementation)
 *
 ****************************************************************************/

#include "StreamAttack.hpp"

#include <gz/common/Console.hh>

bool StreamAttack::init(const std::string &stream_attack_topic)
{
	return _node.Subscribe(stream_attack_topic, &StreamAttack::onStreamCmd, this);
}

//////////////////////////////////////////////////
void StreamAttack::onStreamCmd(const gz::msgs::Int32 &msg)
{
	_stream_attack_option = msg.data();
	gzdbg << "UAViSec Stream Attack Option Received: " << _stream_attack_option << std::endl;
}

// 3. Aplica o ataque baseado na opcao recebida
void StreamAttack::apply(cv::Mat &frame, int width, int height) const
{
	if (_stream_attack_option == 1) {
		cv::flip(frame, frame, 0); // Inverte a imagem (Flip)
	}
	else if (_stream_attack_option == 2) {
		// Adiciona a mascara/ruido no centro. Tamanho proporcional
		// ao frame (1/6 da menor dimensao) em vez de fixo em
		// pixels, para continuar visivel em qualquer resolucao.
		const int squareSize = std::min(width, height) / 6;
		cv::Mat mask = cv::Mat::zeros(height, width, CV_8UC1);
		cv::Point center(width/2, height/2);
		cv::Point topLeft(center.x - squareSize/2, center.y - squareSize/2);
		cv::Rect square(topLeft.x, topLeft.y, squareSize, squareSize);
		cv::rectangle(mask, square, cv::Scalar(255), cv::FILLED);
		frame.setTo(cv::Scalar(0,0,0), mask);
	}
}