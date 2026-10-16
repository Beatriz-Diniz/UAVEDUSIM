/****************************************************************************
 *
 *   UAVJamSim - Camera Stream Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/msgs.hh>
#include <gz/transport.hh>

#include <opencv2/opencv.hpp>

class StreamAttack
{
public:
	explicit StreamAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &stream_attack_topic = "/gazebo/default/attack/stream_cmd");

	// Opcao ativa: 0=desativado, 1=flip, 2=mascara/ruido no centro.
	int option() const { return _stream_attack_option; }

	// Aplica o ataque no frame in-place. width/height sao as dimensoes do
	// proprio frame, usadas para calcular a mascara centralizada da opcao 2.
	void apply(cv::Mat &frame, int width, int height) const;

private:
	void onStreamCmd(const gz::msgs::Int32 &msg);

	gz::transport::Node &_node;

	int _stream_attack_option{0};
};