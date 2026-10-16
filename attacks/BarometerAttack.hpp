/****************************************************************************
 *
 *   UAVJamSim - Barometer Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/msgs.hh>
#include <gz/transport.hh>

class BarometerAttack
{
public:
	explicit BarometerAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &baro_attack_topic = "/gazebo/default/attack/baro");

	// Opcao ativa (0=desativado, 1=offset de altitude) e offset atual em metros.
	int option() const { return _baro_attack_option; }
	double altOffsetM() const { return _baro_alt_offset; }

	// Converte o offset de altitude configurado em pressao/temperatura de
	// saida, a partir da leitura nominal (raw_pressure/raw_temperature).
	// Se nenhum ataque estiver ativo, out_pressure/out_temperature saem
	// identicos aos valores nominais recebidos.
	void apply(float raw_pressure, float raw_temperature,
		   float &out_pressure, float &out_temperature) const;

private:
	void baroAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	int _baro_attack_option{0};
	double _baro_alt_offset{0.0};
};