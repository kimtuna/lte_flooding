epc 띄우기
sudo ~/srsRAN_4G/build/srsepc/src/srsepc ~/.config/srsran/epc.conf

enb 띄우기
cd /home/parklab/.config/srsran/
   sudo ~/srsRAN_4G/build/srsenb/src/srsenb enb.conf --rf.device_args "type=b200,serial=34C78C0"

usrp 확인
uhd_find_devices

flooding 실행
python3 lte_flooding.py --usrp-args "serial=34C78E4" --mcc 123 --mnc 456 --earfcn 1650
