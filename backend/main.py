from fastapi import FastAPI
from fastapi import HTTPException
from dotenv import load_dotenv
import os
import supabase
import secrets
from datetime import date,datetime,timedelta,timezone
from typing import List,Optional
from pydantic import BaseModel
from email_validator import validate_email, EmailNotValidError
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
from types import SimpleNamespace
app= FastAPI()
load_dotenv()
URL = os.getenv('SUPABASE_URL')
anon_key = os.getenv('SUPABASE_ANON_KEY')
service_role_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
client= supabase.create_client(URL,service_role_key)
class SendCodeRequest(BaseModel):
    email:str
@app.get("/health")
def health():
    return {"ok":True}
@app.get("/db_test")
def db_test():
    client.table("sessions").insert({"topic": "연결 테스트"}).execute()
    result= client.table("session").select("*").execute()
    return result.data
@app.post("/auth/send-code")
def send_code(req:SendCodeRequest):
    try:
        if req.email[-5:] == "ac.kr":
            info= validate_email(req.email.strip())
        else:
            raise HTTPException(status_code=400,detail="학교 이메일(ac.kr)만 사용 가능해요.")
    except EmailNotValidError:
        raise HTTPException(status_code=400,detail="이메일을 형식을 확인해주세요.")
    try:
        auth_client = supabase.create_client(URL, anon_key)
        auth_client.auth.sign_in_with_otp({"email": info.normalized})
    except Exception as e:
        print("send-code 실패:", e)
        raise HTTPException(status_code=429, detail="잠시 후 다시 시도해 주세요.")
    
    return {"sent":True}
class VerifyRequest(BaseModel):
    email:str
    code:str
@app.post("/auth/verify")
def verify(req:VerifyRequest):
    code=req.code.strip()
    email= req.email.strip()
    try:
        if not (code.isdigit() and len(code) == 8):
            raise HTTPException(status_code=400,detail="인증코드가 맞지 않거나 만료되었습니다.")
        auth_client = supabase.create_client(URL, anon_key)
        result = auth_client.auth.verify_otp({
            "email": email,
            "token": code,
            "type": "email",
        })
    except:
        raise HTTPException(status_code=400,detail="인증코드가 맞지 않거나 만료되었습니다.")
    return{"access_token": result.session.access_token,
    "refresh_token": result.session.refresh_token}
# ============================================
# ④ 지금 요청한 사람이 누구인지 확인하는 함수
# ============================================
bearer = HTTPBearer()   # 요청 헤더의 "Authorization: Bearer 토큰"을 꺼내 주는 도구

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    token = creds.credentials
    try:
        r = httpx.get(
            f"{URL}/auth/v1/user",                       # Supabase의 "내 정보" 주소
            headers={
                "apikey": anon_key,                      # 우리 프로젝트 키
                "Authorization": f"Bearer {token}",      # 학생의 로그인 토큰
            },
            timeout=10,
        )
    except Exception as e:
        print("get_current_user 연결 실패:", e)
        raise HTTPException(status_code=401, detail="다시 로그인해 주세요.")

    if r.status_code != 200:
        print("get_current_user 실패:", r.status_code, r.text[:200])
        raise HTTPException(status_code=401, detail="다시 로그인해 주세요.")

    data = r.json()
    return SimpleNamespace(id=data["id"], email=data.get("email"))
# ============================================
# ⑤-1 내 정보 보기
# ============================================
@app.get("/me")
def me(user = Depends(get_current_user)):   # 이 API 전에 get_current_user가 먼저 실행됨
    rows = (
        client.table("profiles")             # DB 작업은 맨 위의 service_role 클라이언트
        .select("*")
        .eq("user_id", user.id)              # 내 user_id인 줄만
        .execute()
        .data
    )

    if not rows:                             # 빈 리스트 = 처음 로그인한 사람
        return {"email": user.email, "needs_profile": True}
    return {"email": user.email, "needs_profile": False, "name": rows[0]["name"]}
# ============================================
# ⑤-2 처음 로그인할 때 이름 + 동의 저장
# ============================================
class ProfileRequest(BaseModel):
    name: str
    agree_terms: bool      # 이용약관 동의
    agree_privacy: bool    # 개인정보 수집·이용 동의

@app.post("/me/profile")
def save_profile(req: ProfileRequest, user = Depends(get_current_user)):
    name = req.name.strip()

    if len(name) < 2:
        raise HTTPException(status_code=400, detail="이름을 2자 이상 입력해 주세요.")

    if not (req.agree_terms and req.agree_privacy):
        raise HTTPException(status_code=400, detail="필수 약관에 모두 동의해 주세요.")

    client.table("profiles").upsert({    # 있으면 고치고, 없으면 새로 넣기
        "user_id": user.id,
        "name":name}).execute()
    return {"saved":True,"name":name}
class RefreshRequest(BaseModel):
    refresh_tocken:str
@app.post("/auth/refresh")
def refresh(req:RefreshRequest):
    try:
        auth_client= supabase.create_client(URL,anon_key)
        result= auth_client.auth.refresh_otp(req.refresh_tocken)
    except Exception as e:
        print(e)
        raise HTTPException(status_code=401,detail="로그인이 필요합니다.")
    return {"access_token":result.session.access_token,
            "refresh_token":result.session.refresh_token}
KST= timezone(timedelta(hours=9))
def today_kst():
    return datetime.now(KST).date()

def make_join_code():
    letters = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # 헷갈리는 0/O, 1/I/L 제외
    return "".join(secrets.choice(letters) for _ in range(6))
class TaskIn(BaseModel):
    title: str
    due: Optional[date] = None

class RoleIn(BaseModel):
    name: str
    description: Optional[str] = None
    tasks: List[TaskIn] = []

class CreateSessionRequest(BaseModel):
    topic: str
    course: Optional[str] = None
    criteria: Optional[str] = None
    output_type: Optional[str] = None     # ppt / report / demo / etc
    presentation: bool = False
    deadline: date
    team_size: int
    roles: List[RoleIn] = []
    
@app.post("/sessions")
def create_session(req: CreateSessionRequest, user = Depends(get_current_user)):
    if not client.table("profiles").select("user_id").eq("user_id", user.id).execute().data:
        raise HTTPException(status_code=400, detail="이름을 먼저 등록해 주세요.")

    # ② 값 검사
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="과제 주제를 입력해 주세요.")
    if req.deadline <= today_kst():                          
        raise HTTPException(status_code=400, detail="마감일은 오늘 이후로 골라 주세요.")
    if not (2 <= req.team_size <= 10):
        raise HTTPException(status_code=400, detail="팀 인원은 2~10명이에요.")

    session = None
    for _ in range(5):
        try:
            session = client.table("sessions").insert({
                "leader_id": user.id,
                "topic": req.topic.strip(),
                "course": req.course,
                "criteria": req.criteria,
                "output_type": req.output_type,
                "presentation": req.presentation,
                "deadline": req.deadline.isoformat(),        # 힌트: date → "2026-12-04" 문자열
                "team_size": req.team_size,
                "join_code": make_join_code(),                    # 힌트: 위의 도우미 함수
            }).execute().data[0]
            break
        except Exception as e:
            print("세션 저장 재시도:", e)
    if session is None:
        raise HTTPException(status_code=500, detail="세션을 만들지 못했어요.")

    # ④ 만든 사람을 팀장으로 members에 추가
    client.table("members").insert({
        "session_id": session["id"],
        "user_id": user.id,
        "is_leader": True,
    }).execute()

    # ⑤ 역할과 할 일 저장
    for i, role in enumerate(req.roles):
        saved_role = client.table("roles").insert({
            "session_id": session["id"],
            "name": role.name,
            "description": role.description,
            "sort_order": i,
        }).execute().data[0]

        for task in role.tasks:
            client.table("tasks").insert({
                "role_id": saved_role["id"],              # 힌트: 방금 저장한 역할의 무엇?
                "title": task.title,
                "due": task.due.isoformat() if task.due else None,
            }).execute()

    # ⑥ 결과
    return {"session_id": session["id"], "join_code": session["join_code"]}