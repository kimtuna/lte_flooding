/**
 * Copyright 2013-2023 Software Radio Systems Limited
 *
 * This file is part of srsRAN.
 *
 * srsRAN is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * srsRAN is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * A copy of the GNU Affero General Public License can be found in
 * the LICENSE file in the top-level directory of this distribution
 * and at http://www.gnu.org/licenses/.
 *
 */

#ifndef SRSUE_ATTACK_UE_H
#define SRSUE_ATTACK_UE_H

#include <atomic>
#include <map>
#include <mutex>
#include <set>
#include <thread>
#include "srsue/hdr/stack/mac/mux.h"
#include "srsran/interfaces/ue_phy_interfaces.h"
#include "srsran/interfaces/ue_rrc_interfaces.h"
#include "srsran/srslog/srslog.h"

namespace srsue {

// Forward declarations
class mux;
class ue_rnti;

/**
 * 공격용 미니 UE 클래스
 * 
 * 목표: PRACH를 주기적으로 송신하고, RAR 수신 시 Msg3만 한 번 보낸 후 침묵
 */
class attack_ue {
public:
  attack_ue();
  ~attack_ue();

  void init(phy_interface_mac_lte* phy_h, rrc_interface_mac* rrc_h, mux* mux_unit, ue_rnti* rntis, class rrc* rrc_ptr = nullptr);

  void start();
  void stop();

  void set_attack_mode(bool enabled);
  void set_prach_period_ms(uint32_t period_ms);
  void set_nof_preambles(uint32_t nof_preambles);

  // RAR 수신 시 호출 (PHY worker 스레드에서)
  void on_rar_received(uint32_t rapid, uint16_t temp_crnti, uint8_t grant[SRSRAN_RAR_GRANT_LEN]);

  // 공격 모드 활성화 여부 확인
  bool is_attack_mode_enabled() const { return attack_mode_enabled.load(); }

private:
  // TX 스레드: PRACH 주기적 송신
  void tx_prach_thread();

  // RX 스레드: RAR 감지 (실제로는 PHY worker 콜백 사용)
  void rx_rar_thread();

  // Msg3 송신 (RAR 수신 후)
  void send_msg3_for_rapid(uint32_t rapid, uint16_t temp_crnti, uint8_t grant[SRSRAN_RAR_GRANT_LEN]);

  // 공유 상태 구조체
  struct attack_context {
    std::mutex mutex;
    std::map<uint32_t, uint16_t> rapid_to_temp_crnti; // RAPID → Temp C-RNTI
    std::map<uint32_t, bool> msg3_sent;               // RAPID별 Msg3 송신 여부
    std::set<uint32_t> active_rapids;                 // 활성 RAPID 목록
    uint32_t prach_period_ms = 20;                   // PRACH 송신 주기 (ms)
    uint32_t nof_preambles   = 64;                    // 사용 가능한 preamble 수
  } ctx;

  std::atomic<bool> running;
  std::atomic<bool> attack_mode_enabled;

  std::thread tx_thread;
  std::thread rx_thread;

  phy_interface_mac_lte* phy_h;
  rrc_interface_mac*    rrc_h;
  mux*                 mux_unit;
  ue_rnti*             rntis;
  class rrc*           rrc_ptr;  // RRC 포인터 (send_con_request 직접 호출용)

  srslog::basic_logger& logger;

  // PRACH 송신 관련
  std::atomic<uint32_t> current_rapid;
  uint32_t              sel_mask_index;
};

} // namespace srsue

#endif // SRSUE_ATTACK_UE_H

