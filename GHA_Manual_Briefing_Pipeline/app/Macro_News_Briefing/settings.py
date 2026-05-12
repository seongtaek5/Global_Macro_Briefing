GUARDRAILS = True
VERBOSE = False

RETRIES = 2
SLEEP_TIME = 5
HOUR_WINDOW = 24

SIM_MODEL = 'all-MiniLM-L6-v2'
SIM_THRESHOLD = 0.60
SUMY_SENTENCE = 2
GPT_SUMMARY_ON = True
EASY_FILTER = False


ASSET_CLASSES = {"Equities" : "Public stocks, earnings, guidance, equity issuance, buybacks, M&A, shareholder actions.", 
                 "Fixed Income" : "Government or corporate bonds, yields, spreads, CDS, ratings, defaults, interest-rate sensitivity.", 
                 "Commodities" : "Physical commodities such as oil, gas, metals, agriculture, inventories, OPEC decisions.", 
                 "Crypto" : "Digital assets such as Bitcoin, Ethereum, stablecoins, crypto exchanges, on-chain activity.", 
                 "Alternatives": "Private equity, private credit, real estate, infrastructure, hedge funds, non-public investments."}

REGIONS = ["U.S", "Developed Markets", "Emerging Markets"]
DELTA = 0.15
FLOOR = 0.45
MAX_LABELS = 3
MODEL = "gpt-5"
SUMMARY_MODEL = "gpt-5-mini"
DEDUP_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0

MAX_ARTICLE_PER_KEYWORD = 100
COMPANY_FILTER_MAX_WORKERS = 10
EXCLUDED_DOMAINS = ["stocktitan.net"]
EXCLUDED_TITLE_KEYWORDS = ["investor alert"]

USABLE_ARTICLE_CAPS = {
    "US_monetary": 5,
    "US_fiscal": 5,
    "US_politics": 5,
    "CN_monetary": 5,
    "CN_fiscal": 5,
    "CN_politics": 5,
    "JP_monetary": 5,
    "JP_fiscal": 5,
    "JP_politics": 5,
    "KR_monetary": 5,
    "KR_fiscal": 5,
    "KR_politics": 5,
    "EU_monetary": 5,
    "EU_politics": 5,
}

SECTION_GROUPS = {
    "United States": ["US_monetary", "US_fiscal", "US_politics"],
    "China": ["CN_monetary", "CN_fiscal", "CN_politics"],
    "Japan": ["JP_monetary", "JP_fiscal", "JP_politics"],
    "Korea": ["KR_monetary", "KR_fiscal", "KR_politics"],
    "EU": ["EU_monetary", "EU_politics"],
}

SECTION_ORDER = [
    "United States",
    "China",
    "Japan",
    "Korea",
    "EU",
]

ASSET_CLASS_SECTIONS = []

FLAT_CATEGORY_SECTIONS = []

PINNED_TOP_COMPANIES = []

MACRO_TOPICS = {
    "US_monetary": "Federal Reserve&Fed&FOMC&Powell&US rate",
    "US_fiscal": "US Treasury&US budget&US tariff&debt ceiling",
    "US_politics": "Trump&White House&Congress&Senate&US election",
    "CN_monetary": "PBOC&China central bank&China rate&RRR cut",
    "CN_fiscal": "China stimulus&China fiscal&local government debt",
    "CN_politics": "Xi Jinping&CCP&China policy&State Council",
    "JP_monetary": "BOJ&Bank of Japan&Ueda&YCC",
    "JP_fiscal": "Japan budget&MOF Japan&Japanese stimulus",
    "JP_politics": "Japan election&Diet Japan&Prime Minister Japan",
    "KR_monetary": "BOK&Bank of Korea&Korea rate&Korean base rate&한국은행",
    "KR_fiscal": "Korea budget&Korean fiscal policy&기획재정부&추경",
    "KR_politics": "Yongsan office&National Assembly Korea&Korean election&대통령실&국회",
    "EU_monetary": "ECB&Lagarde&Eurozone rate&European Central Bank",
    "EU_politics": "European Commission&EU parliament&Brussels policy",
}

NAVER_MACRO_TOPICS = {
    "US_monetary": "Federal Reserve&Fed&FOMC&Powell",
    "US_fiscal": "US Treasury&US budget&US tariff",
    "US_politics": "Trump&White House&Congress&Senate",
    "CN_monetary": "PBOC&중국 인민은행&China central bank",
    "CN_fiscal": "China stimulus&중국 부양책&중국 재정",
    "CN_politics": "Xi Jinping&시진핑&CCP",
    "JP_monetary": "BOJ&Bank of Japan&Ueda&일본은행",
    "JP_fiscal": "Japan budget&일본 재정&일본 정부 지출",
    "JP_politics": "Japan election&일본 총리&일본 정치",
    "KR_monetary": "BOK&Bank of Korea&한국은행&기준금리",
    "KR_fiscal": "한국 예산&기획재정부&추경",
    "KR_politics": "대통령실&국회&한국 정치",
    "EU_monetary": "ECB&Lagarde&Eurozone rate",
    "EU_politics": "EU parliament&European Commission&EU policy",
}

# 출력에서 제외할 토픽 목록
EMAIL_EXCLUDED_COMPANIES = set()

# 별도 한국 금융사 추출에 사용할 회사 목록
KOREA_FINANCE_COMPANIES = [
    "KR_monetary",
    "KR_fiscal",
    "KR_politics",
]

PAIRS = MACRO_TOPICS
NAVER_PAIRS = NAVER_MACRO_TOPICS
REVERSE_PAIRS = {v: k for k, v in PAIRS.items()}

TOPIC_REGION = {}
for region_name, topics in SECTION_GROUPS.items():
    for topic in topics:
        TOPIC_REGION[topic] = region_name
