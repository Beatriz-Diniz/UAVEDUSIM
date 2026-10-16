/****************************************************************************
 *
 *   UAVJamSim - Barometer Attack Module (implementation)
 *
 ****************************************************************************/

#include "BarometerAttack.hpp"

#include <cmath>

#include <px4_platform_common/log.h>

bool BarometerAttack::init(const std::string &baro_attack_topic)
{
	if (!_node.Subscribe(baro_attack_topic, &BarometerAttack::baroAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", baro_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE BAROMETRO - INICIO ========
// Ataque no barometro: configura offset de altitude que sera convertido em perturbacao de pressao.
void BarometerAttack::baroAttackCallback(const gz::msgs::Vector3d &msg)
{

	// msg.x habilita/desabilita o ataque; msg.y define offset de altitude em metros.
	_baro_attack_option = static_cast<int>(msg.x());
	_baro_alt_offset = msg.y();

	PX4_INFO("Baro Attack: Option=%d, Alt_Offset=%.2f m", _baro_attack_option, _baro_alt_offset);
}
// ======== ATAQUE BAROMETRO - FIM ========

// Ataque barometrico: converte offset de altitude em alteracao fisica de pressao.
void BarometerAttack::apply(float raw_pressure, float raw_temperature,
			float &out_pressure, float &out_temperature) const
{
	if (_baro_attack_option == 1) {

		float altitude_offset_m = static_cast<float>(_baro_alt_offset);

		float temperature_k = raw_temperature + 273.15f;

		float adjusted_temperature_k = temperature_k - (0.0065f * altitude_offset_m);

		float pressure_ratio = powf(adjusted_temperature_k / temperature_k, 5.2561f);

		out_pressure = raw_pressure * pressure_ratio;
		out_temperature = adjusted_temperature_k - 273.15f;
	} else {
		out_pressure = raw_pressure;
		out_temperature = raw_temperature;
	}
}