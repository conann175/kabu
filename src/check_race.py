import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers['User-Agent'] = 'Mozilla/5.0'
res = s.get('https://db.netkeiba.com/race/202406010101/')
res.encoding = 'EUC-JP'
soup = BeautifulSoup(res.text, 'html.parser')

# race_dataクラスを探す
for cls in ['race_data', 'data_intro', 'racedata']:
    el = soup.find(class_=cls)
    print(cls, ':', el.text.strip()[:100] if el else 'なし')

# diaryクラスやp要素も確認
diary = soup.find('diary_snap_cut')
print('---')
for span in soup.find_all('span'):
    t = span.text.strip()
    if 'm' in t and ('芝' in t or 'ダ' in t):
        print('span:', t[:100])
        break