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
            earfcn: 주파수 채널 번호 (None이면 모든 주파수 스캔)
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
        
    def create_scanner_config(self) -> str:
        """스캐너용 UE 설정 파일 생성"""
        earfcn_line = ""
        if self.earfcn is not None:
            earfcn_line = f"dl_earfcn = {self.earfcn}"
        else:
            earfcn_line = "# dl_earfcn =  # 모든 주파수 스캔"
        
        config_content = f"""[rf]
device_name = uhd
device_args = {self.usrp_args}
tx_gain = 80
rx_gain = 40

[rat.eutra]
{earfcn_line}

[usim]
mode = soft
algo = milenage
opc  = 63bfa50ee6523365ff14c1f45f88737d
k    = 00112233445566778899aabbccddeeff
imsi = 001010000000001
imei = 353490069873001

[mbms]
service_id_list =

[rf]
nof_antennas = 1

[rat.eutra]
nof_carriers = 1

[expert]
pregenerate_signals = true
"""
        config_path = "srsue_scanner.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def parse_srsue_output(self, line: str) -> Optional[Dict]:
        """srsUE 출력에서 eNB 정보 파싱"""
        enb_info = {}
        
        # PLMN 정보 파싱
        plmn_match = re.search(r'PLMN:\s*MCC=(\d+)\s*MNC=(\d+)', line)
        if plmn_match:
            enb_info['mcc'] = int(plmn_match.group(1))
            enb_info['mnc'] = int(plmn_match.group(2))
            enb_info['plmn'] = f"{plmn_match.group(1)}{plmn_match.group(2)}"
        
        # EARFCN 정보 파싱
        earfcn_match = re.search(r'EARFCN[:\s]+(\d+)', line, re.IGNORECASE)
        if earfcn_match:
            enb_info['earfcn'] = int(earfcn_match.group(1))
        
        # Cell ID 파싱
        cellid_match = re.search(r'Cell[_\s]?ID[:\s]+(\d+)', line, re.IGNORECASE)
        if cellid_match:
            enb_info['cell_id'] = int(cellid_match.group(1))
        
        # RSRP/RSRQ 파싱
        rsrp_match = re.search(r'RSRP[:\s]+([-\d.]+)', line, re.IGNORECASE)
        if rsrp_match:
            enb_info['rsrp'] = float(rsrp_match.group(1))
        
        rsrq_match = re.search(r'RSRQ[:\s]+([-\d.]+)', line, re.IGNORECASE)
        if rsrq_match:
            enb_info['rsrq'] = float(rsrq_match.group(1))
        
        # Bandwidth 파싱
        bw_match = re.search(r'BW[:\s]+(\d+)', line, re.IGNORECASE)
        if bw_match:
            enb_info['bandwidth'] = int(bw_match.group(1))
        
        if enb_info:
            enb_info['timestamp'] = datetime.now().isoformat()
            return enb_info
        
        return None
    
    def scan(self) -> List[Dict]:
        """eNB 스캔 실행"""
        config_path = self.create_scanner_config()
        log_file = "srsue_scanner.log"
        
        logger.info(f"주변 eNB 스캔 시작... (지속 시간: {self.scan_duration}초)")
        if self.earfcn:
            logger.info(f"주파수: EARFCN {self.earfcn}")
        else:
            logger.info("모든 주파수 스캔")
        
        cmd = [
            "srsue",
            config_path,
            "--log.filename", log_file,
            "--log.all_level", "info"
        ]
        
        try:
            self.running = True
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            start_time = time.time()
            seen_enbs = set()
            
            logger.info("스캔 중... (Ctrl+C로 중지 가능)")
            
            while self.running and (time.time() - start_time) < self.scan_duration:
                if self.process.poll() is not None:
                    break
                
                line = self.process.stdout.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                
                # 로그 출력 (verbose 모드는 main에서 설정됨)
                line_stripped = line.strip()
                if line_stripped:
                    # 필요시 디버그 로그로 출력
                    pass
                
                # eNB 정보 파싱
                enb_info = self.parse_srsue_output(line)
                if enb_info:
                    enb_key = (
                        enb_info.get('mcc'),
                        enb_info.get('mnc'),
                        enb_info.get('cell_id')
                    )
                    
                    if enb_key not in seen_enbs:
                        seen_enbs.add(enb_key)
                        self.detected_enbs.append(enb_info)
                        logger.info(f"eNB 탐지: PLMN={enb_info.get('plmn', 'N/A')}, "
                                  f"EARFCN={enb_info.get('earfcn', 'N/A')}, "
                                  f"Cell ID={enb_info.get('cell_id', 'N/A')}, "
                                  f"RSRP={enb_info.get('rsrp', 'N/A')} dBm")
            
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            
        except KeyboardInterrupt:
            logger.info("\n스캔 중지됨")
        except Exception as e:
            logger.error(f"스캔 중 오류: {e}")
        finally:
            self.running = False
        
        return self.detected_enbs
    
    def save_results(self):
        """결과 저장"""
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
            print(f"  PLMN (MCC/MNC): {enb.get('plmn', 'N/A')} "
                  f"({enb.get('mcc', 'N/A')}/{enb.get('mnc', 'N/A')})")
            print(f"  EARFCN: {enb.get('earfcn', 'N/A')}")
            print(f"  Cell ID: {enb.get('cell_id', 'N/A')}")
            if 'rsrp' in enb:
                print(f"  RSRP: {enb['rsrp']} dBm")
            if 'rsrq' in enb:
                print(f"  RSRQ: {enb['rsrq']} dB")
            if 'bandwidth' in enb:
                print(f"  Bandwidth: {enb['bandwidth']} MHz")
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
                print(f"\n[{i}] {enb.get('plmn', 'N/A')} (Cell ID: {enb.get('cell_id', 'N/A')}):")
                print(f"    python3 lte_flooding.py --usrp-args \"{self.usrp_args}\" "
                      f"--mcc {enb.get('mcc')} --mnc {enb.get('mnc')} "
                      f"--earfcn {enb.get('earfcn')}")
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

