#!/usr/bin/env python3
"""
eNB Scout Module
eNB를 찾는 기능을 담당합니다.
"""

import subprocess
import time
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_config_files(config_dir: str = "ue_configs") -> list[str]:
    """ue_configs 폴더에서 모든 config 파일 목록 가져오기"""
    config_files = []
    if os.path.exists(config_dir) and os.path.isdir(config_dir):
        for file in os.listdir(config_dir):
            if file.endswith('.conf'):
                config_files.append(os.path.join(config_dir, file))
    return sorted(config_files)


def get_config_values(config_path: str) -> dict:
    """config 파일에서 모든 설정 값 읽어오기"""
    values = {
        'usrp_args': None,
        'mcc': None,
        'mnc': None,
        'earfcn': None
    }
    try:
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    if key == 'device_args':
                        values['usrp_args'] = value
                    elif key == 'mcc':
                        try:
                            values['mcc'] = int(value)
                        except:
                            pass
                    elif key == 'mnc':
                        try:
                            values['mnc'] = int(value)
                        except:
                            pass
                    elif key == 'dl_earfcn':
                        try:
                            values['earfcn'] = int(value)
                        except:
                            pass
    except:
        pass
    return values


def run_srsue_with_config(config_path: str, log_file: str, usrp_args: Optional[str] = None) -> subprocess.Popen:
    """단일 config 파일로 srsue 실행"""
    import sys
    
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


def find_enb(config_file: str, usrp_args: Optional[str] = None, max_wait_time: int = 60) -> bool:
    """
    eNB를 찾습니다.
    
    Args:
        config_file: 사용할 config 파일 경로
        usrp_args: USRP 장치 인자
        max_wait_time: 최대 대기 시간 (초)
    
    Returns:
        eNB를 찾았으면 True, 아니면 False
    """
    scout_log = "/tmp/srsue_scout.log"
    
    # 이전 로그 파일 삭제
    if os.path.exists(scout_log):
        try:
            os.remove(scout_log)
        except:
            pass
    
    logger.info(f"eNB 탐색 중... (사용하는 config: {config_file})")
    
    # Scout 프로세스 시작
    scout_process = run_srsue_with_config(config_file, scout_log, usrp_args)
    
    enb_found = False
    start_time = time.time()
    last_log_size = 0
    
    # eNB 찾기 대기
    while not enb_found and (time.time() - start_time) < max_wait_time:
        if scout_process.poll() is not None:
            # 프로세스가 종료됨
            return_code = scout_process.returncode
            logger.warning(f"스카우트 프로세스가 종료되었습니다 (종료 코드: {return_code})")
            break
        
        if os.path.exists(scout_log):
            try:
                with open(scout_log, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                
                # 로그가 업데이트되었는지 확인
                current_log_size = len(log_content)
                if current_log_size > last_log_size:
                    last_log_size = current_log_size
                    # 10초마다 한 번씩 로그 출력
                    elapsed = time.time() - start_time
                    if elapsed % 10 < 0.5:
                        log_lines = log_content.split('\n')
                        if len(log_lines) > 5:
                            logger.info(f"스카우트 로그 (경과 시간: {elapsed:.1f}초):")
                            for line in log_lines[-5:]:
                                if line.strip():
                                    logger.info(f"  {line[:150]}")
                
                # 부정적인 키워드 확인
                no_cell_found = any(keyword in log_content.lower() for keyword in [
                    'could not find any cell',
                    'no cell found',
                    'no more frequencies',
                    'did not find any plmn',
                    'completed with failure',
                    'cell search completed. no cells found'
                ])
                
                # PBCH 디코딩 실패 확인 (경고용)
                pbch_decode_failed = any(keyword in log_content.lower() for keyword in [
                    'found pss but could not decode pbch',
                    'could not decode pbch'
                ])
                
                # PBCH 디코딩 성공 확인
                pbch_decoded = any(keyword in log_content.lower() for keyword in [
                    'pbch decoded',
                    'decoded pbch',
                    'mib decoded',
                    'mib received',
                    'system information',
                    'sib1',
                    'sib1 received',
                    'synchronized to cell',
                    'cell synchronized',
                    'found cell',
                    'cell found'
                ])
                
                # 긍정적인 키워드 확인 (더 많은 키워드 추가)
                cell_found_positive = any(keyword in log_content.lower() for keyword in [
                    'found plmn id',
                    'found plmn',
                    'plmn found',
                    'found cell with pci',
                    'detected cell with pci',
                    'cell with pci',
                    'synchronized to cell',
                    'cell synchronized',
                    'cell found with pci',
                    'cell found',
                    'found cell',
                    'rrc connection request',
                    'random access',
                    'rach',
                    'attach request',
                    'sending rrc',
                    'rrc connected',
                    'found peak',
                    'cell_id:',
                    'found peak psr',
                    'cell search: [',
                    'pci:',
                    'pci =',
                    'pss found',
                    'sss found',
                    'found pss',
                    'found sss',
                    'peak found',
                    'mib decoded successfully',
                    'sib decoded'
                ])
                
                # 실제 연결 시도 확인
                actual_connection_attempt = any(keyword in log_content.lower() for keyword in [
                    'rrc connection request',
                    'random access',
                    'rach',
                    'attach request',
                    'sending rrc',
                    'rrc connected',
                    'synchronized to cell'
                ])
                
                # "found peak"와 "cell_id:"가 함께 있으면 셀을 찾은 것
                found_peak_with_cell_id = ('found peak' in log_content.lower() and 'cell_id:' in log_content.lower())
                
                # 셀 찾기 판단 (더 관대한 조건)
                # PBCH 디코딩 성공만으로도 셀을 찾은 것으로 간주
                cell_found = (cell_found_positive and not no_cell_found) or found_peak_with_cell_id or pbch_decoded
                
                # 디버깅 정보 (더 자세한 로그 출력)
                if cell_found_positive or found_peak_with_cell_id or pbch_decoded:
                    matched_keywords = [kw for kw in [
                        'found plmn id', 'found plmn', 'plmn found', 'found cell with pci', 
                        'detected cell with pci', 'synchronized to cell', 'cell synchronized',
                        'cell found with pci', 'cell found', 'found cell', 'rrc connection request',
                        'random access', 'rach', 'attach request', 'sending rrc', 'rrc connected',
                        'found peak', 'cell_id:', 'found peak psr', 'cell search: [',
                        'pci:', 'pss found', 'sss found', 'mib decoded', 'sib decoded'
                    ] if kw in log_content.lower()]
                    if matched_keywords:
                        logger.info(f"셀 발견 키워드 매칭: {matched_keywords}")
                        if pbch_decoded:
                            logger.info("✓ PBCH/MIB 디코딩 성공 확인됨")
                        elif pbch_decode_failed:
                            logger.warning("⚠ PBCH 디코딩 실패 - 셀은 찾았지만 디코딩 실패 (공격은 진행합니다)")
                        if actual_connection_attempt:
                            logger.info("✓ 실제 연결 시도 확인됨")
                        if found_peak_with_cell_id:
                            logger.info("✓ 'Found peak'와 'Cell_id:' 발견 - 셀을 찾았습니다!")
                
                # 추가 디버깅: 최근 로그 라인 출력 (셀 관련 메시지가 있는지 확인)
                if elapsed > 5 and elapsed % 5 < 0.5:  # 5초마다 최근 로그 확인
                    recent_lines = log_content.split('\n')[-10:]
                    cell_related = [line for line in recent_lines if any(kw in line.lower() for kw in [
                        'cell', 'pci', 'pss', 'sss', 'pbch', 'mib', 'sib', 'plmn', 'synchronized', 'peak'
                    ])]
                    if cell_related:
                        logger.debug(f"최근 셀 관련 로그 ({len(cell_related)}개):")
                        for line in cell_related[-3:]:  # 최근 3개만
                            if line.strip():
                                logger.debug(f"  {line[:200]}")
                
                if cell_found:
                    enb_found = True
                    logger.info("✓ eNB를 찾았습니다!")
                    break
            except Exception as e:
                logger.debug(f"로그 파일 읽기 오류: {e}")
        
        time.sleep(0.5)
    
    # 스카우트 프로세스 종료
    if scout_process.poll() is None:
        scout_process.terminate()
        try:
            scout_process.wait(timeout=2)
        except:
            scout_process.kill()
    
    # eNB를 찾지 못한 경우 로그 파일 경로 안내
    if not enb_found:
        logger.warning(f"eNB를 찾지 못했습니다. 로그 파일을 확인하세요: {scout_log}")
        logger.info("로그에서 'cell', 'pci', 'pss', 'sss', 'pbch', 'mib', 'plmn' 등의 키워드를 검색해보세요.")
    
    return enb_found

