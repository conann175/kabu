import requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers['User-Agent'] = 'Mozilla/5.0'
res = s.get('https://db.netkeiba.com/horse/ped/2022106045/')
res.encoding = 'EUC-JP'
soup = BeautifulSoup(res.text, 'html.parser')
ped = soup.find('table', class_='blood_table')
rows = ped.find_all('tr')

def clean(td):
    if td:
        return td.text.strip().split(chr(10))[0].strip()
    return None

print('father:', clean(rows[0].find_all('td')[0]))
print('father_father:', clean(rows[0].find_all('td')[1]))
print('father_mother:', clean(rows[8].find_all('td')[0]))
print('mother:', clean(rows[16].find_all('td')[0]))
print('mother_father:', clean(rows[16].find_all('td')[1]))
print('mother_mother:', clean(rows[24].find_all('td')[0]))