import os
from dotenv import load_dotenv

load_dotenv(".env")

CLIENT_ID = os.getenv("UPWORK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("UPWORK_CLIENT_SECRET", "")
ACCESS_TOKEN = os.getenv("UPWORK_ACCESS_TOKEN", "")
REDIRECT_URI = os.getenv("UPWORK_REDIRECT_URI", "http://localhost:8080/callback")
TIMEZONE = os.getenv("TIMEZONE", "UTC")

GRAPHQL_URL = "https://api.upwork.com/graphql"
AUTH_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
TOKEN_CACHE_FILE = ".token_cache.json"
DB_PATH = "upwork_jobs.db"

TECH_SKILLS = [
    "Python", "JavaScript", "TypeScript", "React", "Next.js", "Vue.js",
    "Angular", "PHP", "Laravel", "Django", "FastAPI", "Flask",
    "Java", "Spring Boot", "Kotlin", "Swift", "Go", "Rust", "C#", "C++",
    "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "Linux",
    "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch",
    "OpenAI", "LangChain", "RAG", "LLM", "Computer Vision", "NLP",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Supabase", "Firebase",
    "React Native", "Flutter", "iOS", "Android",
    "GraphQL", "REST API", "Microservices", "DevOps", "CI/CD", "Git",
    "Data Analysis", "Pandas", "Power BI", "Tableau", "SQL",
    "Web Scraping", "Selenium", "Playwright",
    "Solidity", "Web3", "Blockchain",
    "WordPress", "Shopify", "WooCommerce", "Webflow",
    "Node.js", "HTML", "CSS",
]

CATEGORIES = [
    "Web, Mobile & Software Dev",
    "IT & Networking",
    "Data Science & Analytics",
    "Engineering & Architecture",
    "Design & Creative",
    "Sales & Marketing",
    "Writing",
]

CONTRACTOR_TIERS = ["ENTRY_LEVEL", "INTERMEDIATE", "EXPERT"]
