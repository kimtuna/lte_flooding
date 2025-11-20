epc 띄우기
sudo ~/srsRAN_4G/build/srsepc/src/srsepc ~/.config/srsran/epc.conf

enb 띄우기
cd /home/parklab/.config/srsran/
   sudo ~/srsRAN_4G/build/srsenb/src/srsenb enb.conf --rf.device_args "type=b200,serial=34C78C0"

usrp 확인
uhd_find_devices

flooding 실행
python3 lte_flooding.py --usrp-args "serial=34C78E4" --mcc 123 --mnc 456 --earfcn 1650


imsi,imei는 계속해서 생성하고 있는데 usim값은 고정으로 보내지고 있음. 왜냐하면 원래는 epc까지 공격하려고 했는데 그럴 필요는 없는거 같아서 msg3까지 마무리 하려고 함.