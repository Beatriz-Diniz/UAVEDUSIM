/****************************************************************************
 *
 *   UAVJamSim - Magnetometer Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/msgs.hh>
#include <gz/transport.hh>

class MagnetometerAttack
{
public:
	explicit MagnetometerAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &mag_attack_topic = "/gazebo/default/attack/mag");

	// Opcao ativa (0=desativado, 1=troca de eixos X/Y).
	int option() const { return _mag_attack_option; }

	// Aplica o ataque: opcao 1 troca os eixos X e Y do campo magnetico;
	// caso contrario, repassa os valores brutos sem alteracao.
	void apply(float raw_x, float raw_y, float raw_z,
		   float &out_x, float &out_y, float &out_z) const;

private:
	void magAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	int _mag_attack_option{0};
};