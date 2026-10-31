import json
import math
import os
from decimal import Decimal

import requests
import streamlit as st
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool


# 1. 환경변수와 화면 설정
load_dotenv()

st.set_page_config(
    page_title="환율 조회 웹앱",
    page_icon="💱",
)

CURRENCIES = ["KRW", "USD", "EUR", "JPY", "GBP"]


# 2. Frankfurter에서 환율 조회
@st.cache_data(ttl=300)
def get_rate(base, quote):
    response = requests.get(
        f"https://api.frankfurter.dev/v2/rate/{base}/{quote}",
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    rate = float(data["rate"])

    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("유효하지 않은 환율입니다.")

    if data["base"].upper() != base or data["quote"].upper() != quote:
        raise ValueError("요청한 통화와 응답 통화가 다릅니다.")

    return {
        "base": base,
        "quote": quote,
        "rate": str(data["rate"]),
        "date": data["date"],
    }


# 3. 금액 환산
def convert_currency(base, quote, amount):
    base = base.upper()
    quote = quote.upper()

    if base not in CURRENCIES or quote not in CURRENCIES:
        raise ValueError("지원 통화는 KRW, USD, EUR, JPY, GBP입니다.")

    number = Decimal(str(amount))

    if not number.is_finite() or number < 0:
        raise ValueError("금액은 0 이상의 숫자로 입력해 주세요.")

    # 같은 통화라면 API를 호출하지 않음
    if base == quote:
        return {
            "base": base,
            "quote": quote,
            "amount": str(number),
            "rate": "1",
            "converted": str(number),
            "date": None,
        }

    result = get_rate(base, quote)

    return {
        **result,
        "amount": str(number),
        "converted": str(number * Decimal(result["rate"])),
    }


# 4. 에이전트가 사용할 환율 조회 도구
@tool
def exchange_tool(base: str, quote: str, amount: float = 1) -> str:
    """최신 일별 환율로 금액을 환산합니다.
    base와 quote는 KRW, USD, EUR, JPY, GBP 중 하나입니다.
    amount는 기준 통화의 금액입니다.
    """
    try:
        result = convert_currency(base, quote, amount)
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return json.dumps({
            "error": "환율을 조회하지 못했습니다. 통화·금액·네트워크를 확인하세요."
        }, ensure_ascii=False)


# 5. 화면 구성
st.title("💱 환율 조회 웹앱")
st.caption(
    "최신 일별 기준 환율을 사용합니다. "
    "실시간 매매 환율이나 은행 수수료는 반영하지 않습니다."
)

tab1, tab2 = st.tabs(["직접 환율 조회", "환율 챗봇"])


# 6. 직접 조회 화면
with tab1:
    with st.form("exchange_form"):
        base = st.selectbox(
            "기준 통화",
            CURRENCIES,
            index=1,
        )
        quote = st.selectbox(
            "변환할 통화",
            CURRENCIES,
            index=0,
        )
        amount = st.number_input(
            "금액",
            min_value=0.0,
            value=100.0,
            step=10.0,
        )

        submitted = st.form_submit_button("환율 조회하기")

    if submitted:
        try:
            with st.spinner("환율 조회 중입니다..."):
                result = convert_currency(base, quote, amount)

            if base == quote:
                st.info("같은 통화끼리는 환율 조회가 필요 없습니다.")

            st.metric(
                "환산 금액",
                f"{Decimal(result['converted']):,.2f} {quote}",
            )

            st.write(
                f"1 {base} = "
                f"{Decimal(result['rate']):,.6f} {quote}"
            )

            if result["date"]:
                st.write(f"환율 기준일: {result['date']}")
                st.caption("출처: Frankfurter")

        except Exception:
            st.error(
                "환율을 조회하지 못했습니다. "
                "네트워크 연결을 확인하고 다시 시도해 주세요."
            )


# 7. 챗봇 화면
with tab2:
    st.subheader("환율 챗봇")
    st.info("예시: 100달러는 원화로 얼마야?")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if st.button("대화 지우기"):
        st.session_state.messages = []
        st.rerun()

    # 이전 대화 출력
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])

    api_key = os.getenv("OPENAI_API_KEY", "")

    if not api_key:
        st.warning(
            "챗봇을 사용하려면 .env에 OPENAI_API_KEY를 설정하세요. "
            "직접 환율 조회는 키 없이 사용할 수 있습니다."
        )

    prompt = st.chat_input(
        "환율이나 환산 금액을 질문해 보세요.",
        disabled=not bool(api_key),
    )

    if prompt:
        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
        })

        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            try:
                # 수업과 동일한 모델 → 도구 → 에이전트 생성 순서
                model = init_chat_model(
                    "gpt-5.4-mini",
                    api_key=api_key,
                    reasoning_effort="none",
                    timeout=20,
                    max_retries=0,
                )

                agent = create_agent(
                    model,
                    tools=[exchange_tool],
                    system_prompt=(
                        "당신은 한국어 환율 도우미입니다. "
                        "환율이나 환산 금액을 답할 때는 매번 "
                        "exchange_tool을 호출하세요. "
                        "달러는 USD, 원화는 KRW, 유로는 EUR, "
                        "엔화는 JPY, 파운드는 GBP입니다. "
                        "통화나 금액이 모호하면 먼저 질문하세요. "
                        "도구 결과에 없는 환율이나 날짜를 만들지 마세요. "
                        "도구 오류가 나면 조회 실패를 안내하세요. "
                        "기준 통화, 대상 통화, 환율, 환산 금액, "
                        "실제 기준일을 포함해 간결하게 답하세요. "
                        "실시간 체결가가 아닌 일별 기준 환율임을 알리세요."
                    ),
                )

                with st.spinner("환율을 확인하고 있습니다..."):
                    response = agent.invoke(
                        {
                            "messages":
                                st.session_state.messages[-12:]
                        },
                        config={"recursion_limit": 8},
                    )

                answer = response["messages"][-1].content
                st.write(answer)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                })

            except Exception:
                st.error(
                    "답변을 생성하지 못했습니다. "
                    "OpenAI API 키, 사용 한도, 네트워크를 확인하세요."
                )
                # 실패한 질문은 대화 기록에서 제거
                st.session_state.messages.pop()