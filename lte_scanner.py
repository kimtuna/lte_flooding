#!/usr/bin/env python3
"""
LTE eNB Scanner
USRP 장치를 사용하여 주변 eNB를 탐지하고 정보를 수집합니다.
"""

import subprocess
import time
import signal
import sys
import os
import argparse
import logging
import json
import re
from typing import Dict, List, Optional
from datetime import datetime

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LTEScanner:
    """주변 eNB를 탐지하는 클래스"""
    
    def __init__(self, usrp_args: str, earfcn: Optional[int] = None,
                 scan_duration: int = 30, output_file: Optional[str] = None):
        """
        Args:
            usrp_args: USRP 장치 인자 (예: "serial=30AD123")
            earfcn: 주파수 채널 번호 (None이면 한국 통신사 주요 주파수 순차 스캔)
            scan_duration: 스캔 지속 시간 (초)
            output_file: 결과를 저장할 파일 경로
        """
        self.usrp_args = usrp_args
        self.earfcn = earfcn
        self.scan_duration = scan_duration
        self.output_file = output_file
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.detected_enbs: List[Dict] = []
        
        # 한국 통신사 주요 주파수 대역 (EARFCN)
        # - 800MHz (Band 5): 2400-2649
        # - 1800MHz (Band 3): 1200-1949  
        # - 2100MHz (Band 1): 0-599
        # - 2600MHz (Band 7): 2750-3449
        self.korean_earfcns = [100, 300, 500, 1200, 1500, 1800, 2400, 2600, 3000, 3400]
        
    def create_scanner_config(self, earfcn: Optional[int] = None) -> str:
        """스캐너용 UE 설정 파일 생성"""
        if earfcn is None:
            earfcn = self.earfcn
        
        if earfcn is not None:
            earfcn_line = f"dl_earfcn = {earfcn}"
        else:
            # 주파수를 지정하지 않으면 기본값으로 1800MHz 대역 사용 (한국 통신사 주요 주파수)
            earfcn_line = "dl_earfcn = 1500"
        
        config_content = f"""[rf]
device_name = uhd
device_args = {self.usrp_args}
tx_gain = 80
rx_gain = 40
nof_antennas = 1

[rat.eutra]
{earfcn_line}
nof_carriers = 1

[usim]
mode = soft
algo = milenage
opc  = 63bfa50ee6523365ff14c1f45f88737d
k    = 00112233445566778899aabbccddeeff
imsi = 001010000000001
imei = 353490069873001
"""
        config_path = "srsue_scanner.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def parse_srsue_output(self, line: str) -> Optional[Dict]:
        """srsUE 출력에서 eNB 정보 파싱"""
        if not line or not line.strip():
            return None
        
        line_lower = line.lower()
        
        # eNB 발견 관련 키워드 확인
        # "Could not find Home PLMN" 같은 라인은 파싱하지 않음
        if 'could not find' in line_lower or 'trying to connect' in line_lower:
            return None
        
        if not any(keyword in line_lower for keyword in ['cell', 'plmn', 'earfcn', 'found', 'detected', 'rsrp', 'rsrq', 'pci']):
            return None
        
        enb_info = {}
        
        # 형식: "Found PLMN:  Id=45006, TAC=17000"
        plmn_id_match = re.search(r'Found PLMN[:\s]+Id=(\d+)', line, re.IGNORECASE)
        if plmn_id_match:
            plmn_id = plmn_id_match.group(1)
            # PLMN ID는 보통 5-6자리 (MCC 3자리 + MNC 2-3자리)
            if len(plmn_id) == 5:
                enb_info['mcc'] = int(plmn_id[:3])
                enb_info['mnc'] = int(plmn_id[3:])
                enb_info['plmn'] = plmn_id
            elif len(plmn_id) == 6:
                enb_info['mcc'] = int(plmn_id[:3])
                enb_info['mnc'] = int(plmn_id[3:])
                enb_info['plmn'] = plmn_id
            else:
                # 그 외의 경우 그대로 저장
                enb_info['plmn'] = plmn_id
                # PLMN ID에서 MCC/MNC 추출 시도
                if len(plmn_id) >= 3:
                    enb_info['mcc'] = int(plmn_id[:3])
                    if len(plmn_id) >= 5:
                        enb_info['mnc'] = int(plmn_id[3:5])
                    elif len(plmn_id) >= 4:
                        enb_info['mnc'] = int(plmn_id[3:])
        
        # TAC 파싱
        tac_match = re.search(r'TAC[=:\s]+(\d+)', line, re.IGNORECASE)
        if tac_match:
            enb_info['tac'] = int(tac_match.group(1))
        
        # 형식: "Found Cell:  Mode=FDD, PCI=460, PRB=50, Ports=2, CP=Normal, CFO=-1.7 KHz"
        # PCI (Physical Cell ID) 파싱
        pci_match = re.search(r'PCI[=:\s]+(\d+)', line, re.IGNORECASE)
        if pci_match:
            enb_info['pci'] = int(pci_match.group(1))
            # PCI를 Cell ID로도 사용 (실제 Cell ID는 별도로 받아야 하지만 일단 PCI 사용)
            if 'cell_id' not in enb_info:
                enb_info['cell_id'] = int(pci_match.group(1))
        
        # PRB (Physical Resource Block) 파싱
        prb_match = re.search(r'PRB[=:\s]+(\d+)', line, re.IGNORECASE)
        if prb_match:
            enb_info['prb'] = int(prb_match.group(1))
            # PRB를 bandwidth로 변환 (PRB * 0.18 MHz, 대략적으로)
            prb_value = int(prb_match.group(1))
            if prb_value == 6:
                enb_info['bandwidth'] = 1.4
            elif prb_value == 15:
                enb_info['bandwidth'] = 3
            elif prb_value == 25:
                enb_info['bandwidth'] = 5
            elif prb_value == 50:
                enb_info['bandwidth'] = 10
            elif prb_value == 75:
                enb_info['bandwidth'] = 15
            elif prb_value == 100:
                enb_info['bandwidth'] = 20
        
        # 다양한 PLMN 정보 형식 파싱
        # 형식 1: PLMN: MCC=123 MNC=456
        if 'mcc' not in enb_info:
            plmn_match = re.search(r'PLMN[:\s]*MCC[=:\s]*(\d+)[\s,]+MNC[=:\s]*(\d+)', line, re.IGNORECASE)
            if not plmn_match:
                # 형식 2: MCC: 123, MNC: 456
                plmn_match = re.search(r'MCC[:\s]+(\d+).*?MNC[:\s]+(\d+)', line, re.IGNORECASE)
            if not plmn_match:
                # 형식 3: PLMN: 123456
                plmn_match = re.search(r'PLMN[:\s]+(\d{5,6})', line, re.IGNORECASE)
                if plmn_match:
                    plmn_str = plmn_match.group(1)
                    if len(plmn_str) == 5:
                        enb_info['mcc'] = int(plmn_str[:3])
                        enb_info['mnc'] = int(plmn_str[3:])
                        enb_info['plmn'] = plmn_str
                    elif len(plmn_str) == 6:
                        enb_info['mcc'] = int(plmn_str[:3])
                        enb_info['mnc'] = int(plmn_str[3:])
                        enb_info['plmn'] = plmn_str
            
            if plmn_match and 'mcc' not in enb_info:
                enb_info['mcc'] = int(plmn_match.group(1))
                enb_info['mnc'] = int(plmn_match.group(2))
                enb_info['plmn'] = f"{plmn_match.group(1)}{plmn_match.group(2)}"
        
        # EARFCN 정보 파싱 (다양한 형식)
        earfcn_match = re.search(r'earfcn[:\s]+(\d+)', line, re.IGNORECASE)
        if not earfcn_match:
            earfcn_match = re.search(r'dl[_\s]?earfcn[:\s]+(\d+)', line, re.IGNORECASE)
        if earfcn_match:
            enb_info['earfcn'] = int(earfcn_match.group(1))
        
        # Cell ID 파싱 (다양한 형식)
        # 주의: "Could not find Home PLMN Id=00101" 같은 라인에서 잘못 파싱하지 않도록
        if 'cell_id' not in enb_info:
            # "Cell ID=" 형식만 파싱
            cellid_match = re.search(r'cell[_\s]?id[=:\s]+(\d+)', line, re.IGNORECASE)
            if cellid_match:
                enb_info['cell_id'] = int(cellid_match.group(1))
        
        # RSRP 파싱
        rsrp_match = re.search(r'rsrp[=:\s]+([-\d.]+)', line, re.IGNORECASE)
        if rsrp_match:
            enb_info['rsrp'] = float(rsrp_match.group(1))
        
        # RSRQ 파싱
        rsrq_match = re.search(r'rsrq[=:\s]+([-\d.]+)', line, re.IGNORECASE)
        if rsrq_match:
            enb_info['rsrq'] = float(rsrq_match.group(1))
        
        # CFO 파싱
        cfo_match = re.search(r'CFO[=:\s]+([-\d.]+)', line, re.IGNORECASE)
        if cfo_match:
            enb_info['cfo'] = float(cfo_match.group(1))
        
        # 최소한 PCI, Cell ID, 또는 PLMN 정보가 있어야 유효한 eNB 정보로 간주
        if enb_info and ('pci' in enb_info or 'cell_id' in enb_info or 'plmn' in enb_info):
            enb_info['timestamp'] = datetime.now().isoformat()
            return enb_info
        
        return None
    
    def check_usrp_connection(self) -> bool:
        """USRP 장치 연결 확인"""
        logger.info("USRP 장치 연결 확인 중...")
        
        try:
            # uhd_find_devices로 장치 확인
            result = subprocess.run(
                ["uhd_find_devices"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # 시리얼 번호 추출
            serial_match = re.search(r'serial:\s*([^\s,]+)', result.stdout + result.stderr)
            if serial_match:
                found_serial = serial_match.group(1)
                # 사용자가 지정한 시리얼 추출
                user_serial_match = re.search(r'serial=([^\s"]+)', self.usrp_args)
                user_serial = user_serial_match.group(1) if user_serial_match else None
                
                if user_serial and found_serial.upper() == user_serial.upper():
                    logger.info(f"✓ USRP 장치 연결 확인됨: serial={found_serial}")
                    return True
                elif user_serial:
                    logger.warning(f"지정한 시리얼({user_serial})과 발견된 시리얼({found_serial})이 다릅니다")
                    logger.info(f"발견된 장치 사용: serial={found_serial}")
                    return True
                else:
                    # 시리얼이 지정되지 않았으면 첫 번째 장치 사용
                    logger.info(f"✓ USRP 장치 발견: serial={found_serial}")
                    return True
            
            # srsUE로 직접 확인 시도
            test_cmd = [
                "srsue",
                self.create_scanner_config(),
                "--log.all_level", "error",
                "--log.filename", "/dev/null"
            ]
            
            test_process = subprocess.Popen(
                test_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # 3초 동안 실행하여 연결 확인
            time.sleep(3)
            
            if test_process.poll() is None:
                # 프로세스가 실행 중이면 연결 성공 가능성
                test_process.terminate()
                test_process.wait(timeout=2)
                logger.info("✓ USRP 장치 연결 확인됨 (srsUE 실행 가능)")
                return True
            else:
                # 프로세스가 종료되었으면 오류 확인
                _, stderr = test_process.communicate()
                if "error" in stderr.lower() or "failed" in stderr.lower():
                    logger.error("✗ USRP 장치 연결 실패")
                    logger.error(f"오류: {stderr[:200]}")
                    return False
                else:
                    logger.info("✓ USRP 장치 연결 확인됨")
                    return True
                    
        except subprocess.TimeoutExpired:
            logger.warning("USRP 확인 시간 초과")
            return False
        except FileNotFoundError:
            logger.error("✗ srsUE를 찾을 수 없습니다. srsRAN이 설치되어 있는지 확인하세요.")
            return False
        except Exception as e:
            logger.warning(f"USRP 확인 중 오류: {e}")
            # 오류가 있어도 일단 시도는 해보도록
            return True
    
    def scan(self) -> List[Dict]:
        """eNB 스캔 실행"""
        self.running = True  # 스캔 시작 플래그 설정
        
        # USRP 연결 확인
        if not self.check_usrp_connection():
            logger.error("USRP 장치 연결을 확인할 수 없습니다. 계속 진행할까요? (y/n)")
            # 자동으로 계속 진행 (인터랙티브 모드가 아닐 때는)
            logger.warning("연결 확인 실패했지만 계속 진행합니다...")
        
        log_file = "srsue_scanner.log"
        
        # 스캔할 주파수 목록 결정
        if self.earfcn is not None:
            # 특정 주파수만 스캔
            earfcns_to_scan = [self.earfcn]
            logger.info(f"주변 eNB 스캔 시작... (지속 시간: {self.scan_duration}초)")
            logger.info(f"주파수: EARFCN {self.earfcn}")
        else:
            # 여러 주파수를 순차적으로 스캔
            earfcns_to_scan = self.korean_earfcns
            logger.info(f"주변 eNB 스캔 시작... (지속 시간: {self.scan_duration}초)")
            logger.info(f"한국 통신사 주요 주파수 대역 순차 스캔: {len(earfcns_to_scan)}개 주파수")
        
        # 각 주파수별로 스캔
        scan_time_per_freq = self.scan_duration // len(earfcns_to_scan) if len(earfcns_to_scan) > 1 else self.scan_duration
        if scan_time_per_freq < 5:
            scan_time_per_freq = 5  # 최소 5초
        
        try:
            for idx, earfcn in enumerate(earfcns_to_scan):
                if not self.running:
                    break
                    
                logger.info(f"\n[{idx+1}/{len(earfcns_to_scan)}] EARFCN {earfcn} 스캔 중... ({scan_time_per_freq}초)")
                config_path = self.create_scanner_config(earfcn)
                self._scan_single_frequency(config_path, log_file, scan_time_per_freq, earfcn)
        except KeyboardInterrupt:
            logger.info("\n스캔 중지됨")
        except Exception as e:
            logger.error(f"스캔 중 오류: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        finally:
            self.running = False
        
        return self.detected_enbs
    
    def _scan_single_frequency(self, config_path: str, log_file: str, duration: int, earfcn: int):
        """단일 주파수에 대한 스캔 실행"""
        cmd = [
            "srsue",
            config_path,
            "--log.filename", log_file,
            "--log.all_level", "debug"
        ]
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # 초기 연결 확인 (3초 대기)
            time.sleep(3)
            
            if process.poll() is not None:
                # 프로세스가 종료되었으면 오류
                stdout, _ = process.communicate()
                logger.warning(f"EARFCN {earfcn}: srsUE 프로세스가 조기 종료됨")
                return
            
            start_time = time.time()
            seen_enbs = set()
            
            # 로그 파일도 모니터링
            log_lines_read = 0
            
            while self.running and (time.time() - start_time) < duration:
                if process.poll() is not None:
                    break
                
                # stdout에서 읽기
                line = process.stdout.readline()
                if not line:
                    # 로그 파일에서도 읽기 시도
                    try:
                        if os.path.exists(log_file):
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_lines = f.readlines()
                                if len(log_lines) > log_lines_read:
                                    for log_line in log_lines[log_lines_read:]:
                                        line = log_line
                                        log_lines_read = len(log_lines)
                                        break
                    except:
                        pass
                    
                    if not line:
                        time.sleep(0.1)
                        continue
                
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                
                # 디버그: eNB 관련 키워드가 있는 라인만 로깅
                line_lower = line_stripped.lower()
                # 중요한 정보는 항상 출력 (verbose 모드가 아니어도)
                if any(keyword in line_lower for keyword in ['found cell', 'detected cell', 'plmn', 'cell id', 'earfcn', 'rsrp', 'rsrq']):
                    logger.info(f"[srsUE EARFCN {earfcn}] {line_stripped}")
                elif any(keyword in line_lower for keyword in ['cell', 'scan', 'search']):
                    if logging.getLogger().level == logging.DEBUG:
                        logger.debug(f"[srsUE EARFCN {earfcn}] {line_stripped}")
                
                # eNB 정보 파싱
                enb_info = self.parse_srsue_output(line)
                if enb_info:
                    # EARFCN 정보 추가
                    if 'earfcn' not in enb_info:
                        enb_info['earfcn'] = earfcn
                    
                    # 노이즈 필터링: PCI가 0, 1, 2 같은 작은 값이고 PLMN 정보가 없으면 무시
                    pci = enb_info.get('pci') or enb_info.get('cell_id')
                    plmn = enb_info.get('plmn')
                    
                    # PCI가 0-2 범위이고 PLMN이 없으면 노이즈로 간주
                    if pci is not None and pci <= 2 and not plmn:
                        continue
                    
                    # 같은 EARFCN에서 짧은 시간 내에 탐지된 eNB 정보 병합
                    # (여러 라인에 걸쳐 정보가 나오는 경우)
                    current_time = time.time()
                    merge_window = 10  # 10초 이내에 탐지된 정보는 같은 eNB로 간주
                    
                    # 기존 eNB 정보와 병합 시도
                    existing_enb = None
                    
                    for existing in self.detected_enbs:
                        # 같은 EARFCN인지 확인
                        if existing.get('earfcn') != enb_info.get('earfcn'):
                            continue
                        
                        existing_pci = existing.get('pci') or existing.get('cell_id')
                        existing_plmn = existing.get('plmn')
                        
                        # PCI가 같거나, PLMN이 같으면 같은 eNB로 간주
                        pci_match = pci and existing_pci and pci == existing_pci
                        plmn_match = plmn and existing_plmn and plmn == existing_plmn
                        
                        # 같은 EARFCN이고 (PCI 또는 PLMN이 일치하면) 시간이 가까우면 병합
                        if pci_match or plmn_match:
                            # 시간 확인 (최근 탐지된 eNB인지)
                            existing_time_str = existing.get('timestamp')
                            if existing_time_str:
                                try:
                                    # ISO 형식 타임스탬프 파싱
                                    existing_time = datetime.fromisoformat(existing_time_str.replace('Z', '+00:00')).timestamp()
                                    if abs(current_time - existing_time) < merge_window:
                                        existing_enb = existing
                                        break
                                except:
                                    # 파싱 실패 시 그냥 병합
                                    existing_enb = existing
                                    break
                            else:
                                # 타임스탬프가 없으면 그냥 병합
                                existing_enb = existing
                                break
                        
                        # 같은 EARFCN에서 하나는 PLMN만 있고 다른 하나는 PCI만 있으면 병합
                        # 경우 1: existing에 PLMN이 있고 PCI가 없고, 현재에 PCI만 있으면 병합
                        if existing_plmn and not existing_pci and pci and pci > 2 and not plmn:
                            existing_time_str = existing.get('timestamp')
                            if existing_time_str:
                                try:
                                    existing_time = datetime.fromisoformat(existing_time_str.replace('Z', '+00:00')).timestamp()
                                    if abs(current_time - existing_time) < merge_window:
                                        existing_enb = existing
                                        break
                                except:
                                    pass
                        # 경우 2: existing에 PCI가 있고 PLMN이 없고, 현재에 PLMN만 있으면 병합
                        elif existing_pci and not existing_plmn and plmn and not pci:
                            existing_time_str = existing.get('timestamp')
                            if existing_time_str:
                                try:
                                    existing_time = datetime.fromisoformat(existing_time_str.replace('Z', '+00:00')).timestamp()
                                    if abs(current_time - existing_time) < merge_window:
                                        existing_enb = existing
                                        break
                                except:
                                    pass
                    
                    if existing_enb:
                        # 기존 정보와 병합
                        merged = False
                        for key, value in enb_info.items():
                            if value and (key not in existing_enb or existing_enb[key] == 'N/A' or existing_enb[key] is None):
                                existing_enb[key] = value
                                merged = True
                        
                        if merged:
                            logger.info(f"✓ eNB 정보 업데이트: PLMN={existing_enb.get('plmn', 'N/A')}, "
                                      f"EARFCN={existing_enb.get('earfcn', 'N/A')}, "
                                      f"PCI={existing_enb.get('pci', 'N/A')}")
                    else:
                        # 새로운 eNB 추가
                        enb_key = (pci, enb_info.get('earfcn'), plmn)
                        if enb_key not in seen_enbs:
                            seen_enbs.add(enb_key)
                            self.detected_enbs.append(enb_info)
                            logger.info(f"✓ eNB 탐지: PLMN={enb_info.get('plmn', 'N/A')}, "
                                      f"EARFCN={enb_info.get('earfcn', 'N/A')}, "
                                      f"PCI={enb_info.get('pci', 'N/A')}, "
                                      f"Cell ID={enb_info.get('cell_id', 'N/A')}, "
                                      f"RSRP={enb_info.get('rsrp', 'N/A')} dBm")
            
            # 프로세스 종료 전에 PLMN 정보를 읽을 시간을 더 줌
            # 셀을 찾은 후 PLMN을 읽는 데 시간이 필요함
            if process.poll() is None:
                # 종료 전에 3초 더 대기하여 PLMN 정보를 읽을 기회 제공
                # (너무 길면 전체 스캔 시간이 늘어남)
                remaining_time = duration - (time.time() - start_time)
                if remaining_time > 3:
                    logger.debug(f"EARFCN {earfcn}: PLMN 정보 읽기를 위해 추가 대기 중...")
                    time.sleep(3)
                
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            
            # 스캔 완료 후 로그 파일에서 최종 확인 및 PLMN 정보 추출
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                        log_content = f.read()
                        # 로그에서 eNB 관련 정보 추출
                        log_lines = log_content.split('\n')
                        
                        # PCI별로 그룹화하여 PLMN 정보 매칭
                        pci_plmn_map = {}  # {pci: plmn}
                        pci_enb_map = {}   # {pci: enb_info}
                        
                        for log_line in log_lines:
                            if any(kw in log_line.lower() for kw in ['found cell', 'found plmn', 'pci=', 'plmn']):
                                # 로그 라인 파싱 시도
                                enb_info = self.parse_srsue_output(log_line)
                                if enb_info:
                                    if 'earfcn' not in enb_info:
                                        enb_info['earfcn'] = earfcn
                                    
                                    pci = enb_info.get('pci') or enb_info.get('cell_id')
                                    plmn = enb_info.get('plmn')
                                    
                                    if pci:
                                        if pci not in pci_enb_map:
                                            pci_enb_map[pci] = enb_info
                                        else:
                                            # 정보 병합
                                            for key, value in enb_info.items():
                                                if value and (key not in pci_enb_map[pci] or pci_enb_map[pci][key] == 'N/A' or pci_enb_map[pci][key] is None):
                                                    pci_enb_map[pci][key] = value
                                    
                                    if plmn and pci:
                                        pci_plmn_map[pci] = plmn
                        
                        # PLMN 정보가 있는 PCI에 대해 기존 eNB 업데이트
                        for pci, plmn in pci_plmn_map.items():
                            for existing in self.detected_enbs:
                                if existing.get('earfcn') == earfcn:
                                    existing_pci = existing.get('pci') or existing.get('cell_id')
                                    if existing_pci == pci and not existing.get('plmn'):
                                        existing['plmn'] = plmn
                                        existing['mcc'] = int(plmn[:3]) if len(plmn) >= 3 else None
                                        existing['mnc'] = int(plmn[3:]) if len(plmn) >= 5 else None
                                        logger.info(f"✓ PLMN 정보 추가: PCI={pci}, PLMN={plmn}")
                        
                        # 로그에서 발견된 새로운 eNB 정보 추가
                        for pci, enb_info in pci_enb_map.items():
                            if enb_info.get('plmn') or (pci and pci > 2):  # PLMN이 있거나 PCI가 유효한 경우만
                                # 기존 eNB와 병합 확인
                                existing_enb = None
                                for existing in self.detected_enbs:
                                    if existing.get('earfcn') == earfcn:
                                        existing_pci = existing.get('pci') or existing.get('cell_id')
                                        existing_plmn = existing.get('plmn')
                                        enb_pci = enb_info.get('pci') or enb_info.get('cell_id')
                                        enb_plmn = enb_info.get('plmn')
                                        
                                        if (pci and existing_pci and pci == existing_pci) or \
                                           (enb_plmn and existing_plmn and enb_plmn == existing_plmn):
                                            existing_enb = existing
                                            break
                                
                                if existing_enb:
                                    # 병합
                                    for key, value in enb_info.items():
                                        if value and (key not in existing_enb or existing_enb[key] == 'N/A' or existing_enb[key] is None):
                                            existing_enb[key] = value
                                else:
                                    # 새로 추가
                                    enb_key = (pci, earfcn, enb_info.get('plmn'))
                                    if enb_key not in seen_enbs:
                                        seen_enbs.add(enb_key)
                                        self.detected_enbs.append(enb_info)
                except Exception as e:
                    logger.debug(f"로그 파일 분석 중 오류: {e}")
                    
        except Exception as e:
            logger.error(f"EARFCN {earfcn} 스캔 중 오류: {e}")
    
    def save_results(self):
        """결과 저장"""
        # 최종 병합: 같은 EARFCN에서 PLMN과 PCI 정보를 병합
        merged_enbs = []
        
        for enb in self.detected_enbs:
            earfcn = enb.get('earfcn')
            pci = enb.get('pci') or enb.get('cell_id')
            plmn = enb.get('plmn')
            
            # 같은 EARFCN에서 이미 추가된 eNB와 병합 시도
            merged = False
            for merged_enb in merged_enbs:
                if merged_enb.get('earfcn') == earfcn:
                    merged_pci = merged_enb.get('pci') or merged_enb.get('cell_id')
                    merged_plmn = merged_enb.get('plmn')
                    
                    # 병합 조건:
                    # 1. PCI가 같으면 병합
                    # 2. PLMN이 같으면 병합
                    # 3. 같은 EARFCN에서 하나는 PCI만 있고 다른 하나는 PLMN만 있으면 병합 (같은 eNB의 다른 정보)
                    pci_match = pci and merged_pci and pci == merged_pci
                    plmn_match = plmn and merged_plmn and plmn == merged_plmn
                    # 현재 eNB에 PCI만 있고 merged_enb에 PLMN만 있으면 병합
                    # 또는 현재 eNB에 PLMN만 있고 merged_enb에 PCI만 있으면 병합
                    complementary = (pci and not plmn and not merged_pci and merged_plmn) or \
                                   (not pci and plmn and merged_pci and not merged_plmn)
                    
                    if pci_match or plmn_match or complementary:
                        # 정보 병합
                        for key, value in enb.items():
                            if value and (key not in merged_enb or merged_enb[key] == 'N/A' or merged_enb[key] is None):
                                merged_enb[key] = value
                        merged = True
                        break
            
            if not merged:
                merged_enbs.append(enb)
        
        # 노이즈 필터링: PLMN 정보가 없고 PCI가 0-2 범위인 eNB 제거
        filtered_enbs = []
        for enb in merged_enbs:
            pci = enb.get('pci') or enb.get('cell_id')
            plmn = enb.get('plmn')
            
            # PLMN이 있으면 유효한 eNB
            if plmn:
                filtered_enbs.append(enb)
            # PLMN이 없어도 PCI가 3 이상이면 유효한 eNB로 간주
            elif pci and pci > 2:
                filtered_enbs.append(enb)
            # 그 외는 노이즈로 간주하고 제외
        
        self.detected_enbs = filtered_enbs
        
        if not self.detected_enbs:
            logger.warning("탐지된 eNB가 없습니다.")
            return
        
        results = {
            'scan_time': datetime.now().isoformat(),
            'scan_duration': self.scan_duration,
            'earfcn': self.earfcn,
            'total_detected': len(self.detected_enbs),
            'enbs': self.detected_enbs
        }
        
        # 콘솔 출력
        print("\n" + "="*60)
        print("탐지된 eNB 목록")
        print("="*60)
        for i, enb in enumerate(self.detected_enbs, 1):
            print(f"\n[{i}] eNB 정보:")
            if enb.get('plmn'):
                print(f"  PLMN (MCC/MNC): {enb.get('plmn', 'N/A')} "
                      f"({enb.get('mcc', 'N/A')}/{enb.get('mnc', 'N/A')})")
            else:
                print(f"  PLMN (MCC/MNC): N/A")
            print(f"  EARFCN: {enb.get('earfcn', 'N/A')}")
            if 'pci' in enb:
                print(f"  PCI: {enb.get('pci', 'N/A')}")
            if 'cell_id' in enb and enb.get('cell_id') != enb.get('pci'):
                print(f"  Cell ID: {enb.get('cell_id', 'N/A')}")
            if 'rsrp' in enb:
                print(f"  RSRP: {enb['rsrp']} dBm")
            if 'rsrq' in enb:
                print(f"  RSRQ: {enb['rsrq']} dB")
            if 'bandwidth' in enb:
                print(f"  Bandwidth: {enb['bandwidth']} MHz")
            if 'tac' in enb:
                print(f"  TAC: {enb['tac']}")
            print(f"  탐지 시간: {enb.get('timestamp', 'N/A')}")
        print("="*60)
        print(f"\n총 {len(self.detected_enbs)}개의 eNB 탐지됨")
        
        # 파일 저장
        if self.output_file:
            output_path = self.output_file
        else:
            output_path = f"enb_scan_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"결과가 {output_path}에 저장되었습니다.")
        
        # Flooding에 사용할 수 있는 명령어 출력
        if self.detected_enbs:
            print("\n" + "="*60)
            print("Flooding에 사용할 수 있는 명령어:")
            print("="*60)
            for i, enb in enumerate(self.detected_enbs, 1):
                plmn_str = enb.get('plmn', 'N/A')
                pci_str = f"PCI={enb.get('pci', 'N/A')}" if 'pci' in enb else f"Cell ID={enb.get('cell_id', 'N/A')}"
                print(f"\n[{i}] {plmn_str} ({pci_str}):")
                
                mcc = enb.get('mcc')
                mnc = enb.get('mnc')
                earfcn = enb.get('earfcn')
                
                cmd_parts = ["python3", "lte_flooding.py", "--usrp-args", f'"{self.usrp_args}"']
                if mcc is not None:
                    cmd_parts.extend(["--mcc", str(mcc)])
                if mnc is not None:
                    cmd_parts.extend(["--mnc", str(mnc)])
                if earfcn is not None:
                    cmd_parts.extend(["--earfcn", str(earfcn)])
                
                print(f"    {' '.join(cmd_parts)}")
            print("="*60)
    
    def stop(self):
        """스캔 중지"""
        self.running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                try:
                    self.process.kill()
                except:
                    pass


def main():
    global args
    parser = argparse.ArgumentParser(
        description="LTE eNB Scanner - 주변 eNB를 탐지하고 정보를 수집합니다"
    )
    parser.add_argument(
        "--usrp-args",
        type=str,
        required=True,
        help="USRP 장치 인자 (예: serial=30AD123 또는 type=b200)"
    )
    parser.add_argument(
        "--earfcn",
        type=int,
        default=None,
        help="주파수 채널 번호 (지정하지 않으면 모든 주파수 스캔)"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=30,
        help="스캔 지속 시간(초) (기본값: 30)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="결과를 저장할 JSON 파일 경로 (기본값: 자동 생성)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="상세한 로그 출력"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="스캔 완료 후 인터랙티브하게 eNB를 선택하여 flooding 시작"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    scanner = LTEScanner(
        usrp_args=args.usrp_args,
        earfcn=args.earfcn,
        scan_duration=args.duration,
        output_file=args.output
    )
    
    # 시그널 핸들러 설정
    def signal_handler(sig, frame):
        logger.info("\n종료 신호 수신...")
        scanner.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        scanner.scan()
        scanner.save_results()
        
        # 인터랙티브 모드: eNB 선택 후 flooding 시작
        if args.interactive and scanner.detected_enbs:
            print("\n" + "="*60)
            print("인터랙티브 모드: Flooding할 eNB를 선택하세요")
            print("="*60)
            
            while True:
                try:
                    choice = input(f"\n선택할 eNB 번호 (1-{len(scanner.detected_enbs)}) 또는 'q'로 종료: ").strip()
                    
                    if choice.lower() == 'q':
                        print("종료합니다.")
                        break
                    
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(scanner.detected_enbs):
                            selected_enb = scanner.detected_enbs[idx]
                            print(f"\n선택된 eNB: PLMN={selected_enb.get('plmn')}, "
                                  f"EARFCN={selected_enb.get('earfcn')}, "
                                  f"Cell ID={selected_enb.get('cell_id')}")
                            
                            # Flooding 시작
                            import subprocess as sp
                            flooding_cmd = [
                                "python3", "lte_flooding.py",
                                "--usrp-args", args.usrp_args,
                                "--mcc", str(selected_enb.get('mcc')),
                                "--mnc", str(selected_enb.get('mnc')),
                                "--earfcn", str(selected_enb.get('earfcn'))
                            ]
                            
                            print(f"\nFlooding 시작 중...")
                            print(f"명령어: {' '.join(flooding_cmd)}")
                            print("Ctrl+C로 중지할 수 있습니다.\n")
                            
                            # Flooding 프로세스 실행
                            flooding_process = sp.Popen(flooding_cmd)
                            flooding_process.wait()
                            
                            break
                        else:
                            print(f"잘못된 번호입니다. 1-{len(scanner.detected_enbs)} 사이의 숫자를 입력하세요.")
                    except ValueError:
                        print("숫자를 입력하세요.")
                except KeyboardInterrupt:
                    print("\n\n사용자에 의해 중지됨")
                    if 'flooding_process' in locals():
                        flooding_process.terminate()
                    break
                except Exception as e:
                    logger.error(f"오류 발생: {e}")
                    break
        
    except Exception as e:
        logger.error(f"오류 발생: {e}")
        scanner.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()

