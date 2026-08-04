# FM24 闃靛娣卞害鍒嗘瀽鍣?
鍒嗘瀽 Football Manager 2024 鐞冮槦闃靛娣卞害锛屼负 4-2-3-1 闃靛瀷鑷姩閫夊嚭 EA 鏈€浣充笌娆′匠 11 浜猴紝骞剁敓鎴愭繁搴﹀浘涓庤ˉ寮哄缓璁€?
## 鍔熻兘

- 瑙ｆ瀽 FM 瀵煎嚭鐨?RTF 闃靛琛ㄦ牸锛堟敮鎸佷腑鏂囧瓧娈碉細濮撳悕/骞撮緞/浣嶇疆/鑳藉姏/娼滃姏锛?- 璁＄畻姣忎釜鐞冨憳鐨?EA锛堥鏈熻兘鍔涳級锛?  - `骞撮緞 < 鎴愰暱鑷冲勾榫刞 鈫?`EA = CA + (鎴愰暱鑷冲勾榫?- 骞撮緞) 脳 姣忓勾鎴愰暱`锛堜笉瓒呰繃 PA锛?  - `骞撮緞 鈮?鎴愰暱鑷冲勾榫刞 鈫?`EA = CA`
  - 鎴愰暱鑷冲勾榫勶紙榛樿 21锛変笌姣忓勾鎴愰暱鍊硷紙榛樿 20锛夊潎鍙皟鏁?- 鐢ㄥ寛鐗欏埄绠楁硶鍒嗛厤 EA 鏈€浣?11 浜恒€丒A 娆′匠 11 浜?- 鐢熸垚娣卞害鍥撅紝姣忎釜浣嶇疆缁欏嚭琛ュ己鍒嗭紙鍒嗘暟瓒婇珮瓒婇渶瑕佽ˉ寮猴級
- 杈撳嚭鍙鍖?HTML 鎶ュ憡锛堢悆鍦洪樀鍨嬪浘 + 娣卞害鍥撅級
- 鍥惧舰鐣岄潰锛坱kinter锛変笌鍛戒护琛屼袱绉嶄娇鐢ㄦ柟寮?- `rtf_path` 鏀寔 `~` 琛ㄧず鐢ㄦ埛涓荤洰褰曪紝渚夸簬鎹㈡満杩佺Щ

## 鐜瑕佹眰

- Python 3.8+
- 渚濊禆锛歚numpy`銆乣scipy`

```bash
pip install numpy scipy
```

## 浣跨敤鏂规硶

### 鍥惧舰鐣岄潰

```bash
python fm_analysis_gui.py
```

鍦ㄧ晫闈腑锛?1. 閫夋嫨鎴栬緭鍏?RTF 闃靛鏂囦欢璺緞锛堥粯璁よ鍙?`config.json` 涓繚瀛樼殑璺緞锛?2. 璋冩暣 EA 鍏紡鍙傛暟锛堟垚闀胯嚦骞撮緞銆佹瘡骞存垚闀匡級
3. 鐐瑰嚮"寮€濮嬪垎鏋?锛屽畬鎴愬悗鑷姩鎵撳紑 HTML 鎶ュ憡

### 鍛戒护琛?
```bash
python fm_analysis.py
```

榛樿璇诲彇 `config.json` 涓繚瀛樼殑璺緞锛堥粯璁?`~\Documents\Sports Interactive\Football Manager 2024\team.rtf`锛夛紝缁撴灉鍐欏叆 `fm_analysis.html`銆?
### 浣滀负妯″潡璋冪敤

```python
import fm_analysis as fa

roster = fa.read_roster_from_rtf(r"path\to\team.rtf")
html = fa.analyze(roster, growth_until_age=21, growth_per_year=20)
```

## 閰嶇疆鏂囦欢

`config.json` 淇濆瓨 GUI 鐨勯粯璁よ缃紝杩愯鏃跺彲鑷姩璇诲啓锛?
```json
{
  "rtf_path": "~\\Documents\\Sports Interactive\\Football Manager 2024\\team.rtf",
  "growth_until_age": 21,
  "growth_per_year": 20
}
```

- `rtf_path`锛氶樀瀹规枃浠惰矾寰勶紙`~` 灞曞紑涓虹敤鎴蜂富鐩綍锛?- `growth_until_age`锛欵A 鎴愰暱鍋滄骞撮緞
- `growth_per_year`锛欵A 姣忓瞾鎴愰暱鍊?
鏂囦欢缂哄け鎴栨崯鍧忔椂鑷姩鍥為€€鍒伴粯璁ゅ€笺€?
## RTF 鏂囦欢瑕佹眰

浠?FM 鍐呭鍑洪樀瀹硅鍥句负 RTF 鏍煎紡锛岃〃鏍奸渶鍖呭惈浠ヤ笅涓枃瀛楁锛?
| 濮撳悕 | 骞撮緞 | 浣嶇疆 | 鑳藉姏 | 娼滃姏 |
| ---- | ---- | ---- | ---- | ---- |

`鑳藉姏` 鍒楀鍑轰袱浠斤紙褰撳墠/娼滃姏锛夛紝瑙ｆ瀽鍣ㄥ彇绗簩浠戒綔涓哄綋鍓嶈兘鍔?CA銆?
## 椤圭洰缁撴瀯

```
fm_analysis.py       鏍稿績閫昏緫锛歊TF 瑙ｆ瀽銆丒A 璁＄畻銆侀樀瀹瑰垎閰?fm_report.py         HTML 鎶ュ憡娓叉煋锛堜粠 templates/ 璇诲彇妯℃澘锛?fm_analysis_gui.py   tkinter 鍥惧舰鐣岄潰
fm_analysis.html     鐢熸垚鐨勬姤鍛婅緭鍑猴紙琚?gitignore 蹇界暐锛?config.json          閰嶇疆鏂囦欢
templates/style.css      鎶ュ憡鏍峰紡琛?templates/report.html    鎶ュ憡 HTML 楠ㄦ灦锛堝崰浣嶇鏇挎崲锛?```

## EA 琛ュ己鍒嗚鏄?
- 姣忎釜浣嶇疆鏈€澶?4 鍒?- 棣栧彂璇ヤ綅缃悆鍛?EA 浣庝簬鍙傝€冨€硷紙棣?11 浜哄钩鍧?EA 鐨?90%锛?2
- 娆′匠 11 浜鸿浣嶇疆鐞冨憳寮?+1
- 娣卞害鍥捐浣嶇疆寮辩悆鍛樻瘮渚嬶紙1 浣嶅皬鏁帮級+鐩稿簲姣斾緥
