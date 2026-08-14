"""
OpenAI client - OpenAI Compatible API

Environment variables:

OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL

LANGSMITH:
LANGSMITH_TRACING
LANGSMITH_ENDPOINT
LANGSMITH_API_KEY
LANGSMITH_PROJECT
"""


from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv


load_dotenv()


logger = logging.getLogger(__name__)


# =====================================================
# Config
# =====================================================

OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://api.openai.com/v1"
)

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "GPT-5-nano"
)


# GPT-5: forcing temperature is not recommended
OPENAI_TEMPERATURE = os.getenv(
    "OPENAI_TEMPERATURE"
)

OPENAI_MAX_TOKENS = int(
    os.getenv(
        "OPENAI_MAX_TOKENS",
        "4096"
    )
)


_client = None



# =====================================================
# Client
# =====================================================

def _get_client():

    global _client

    if _client is None:

        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured"
            )

        try:
            from openai import OpenAI

        except ImportError:

            raise RuntimeError(
                "Install openai: pip install openai>=1.0"
            )


        _client = OpenAI(

            base_url=OPENAI_BASE_URL,

            api_key=OPENAI_API_KEY,

            timeout=120,

            max_retries=2
        )


    return _client




# =====================================================
# Availability
# =====================================================

def llm_available() -> bool:
    """
    Determine whether the LLM client is available
    """

    if not OPENAI_API_KEY:
        return False

    try:

        import openai

        return True

    except ImportError:

        return False



# =====================================================
# Chat
# =====================================================

def chat(
    messages: list[dict[str,str]],
    *,
    model: Optional[str]=None,
    temperature: Optional[float]=None,
    max_tokens: Optional[int]=None,
    response_format=None
):


    client = _get_client()


    kwargs = {

        "model": model or OPENAI_MODEL,

        "messages": messages,

    }


    # GPT-5: do not force sending temperature
    if temperature is not None:

        kwargs["temperature"] = temperature

    elif OPENAI_TEMPERATURE:

        kwargs["temperature"] = float(
            OPENAI_TEMPERATURE
        )



    if max_tokens is not None:

        kwargs["max_tokens"] = max_tokens

    else:

        kwargs["max_tokens"] = OPENAI_MAX_TOKENS



    if response_format:

        kwargs["response_format"] = response_format



    logger.info(
        "LLM request model=%s",
        kwargs["model"]
    )


    response = client.chat.completions.create(
        **kwargs
    )


    content = (
        response
        .choices[0]
        .message
        .content
        or ""
    )


    return content



# =====================================================
# JSON Chat
# =====================================================

def chat_json(
    messages,
    *,
    model=None,
    temperature=None,
    max_tokens=None
):


    try:

        raw = chat(

            messages,

            model=model,

            temperature=temperature,

            max_tokens=max_tokens,

            response_format={
                "type":"json_object"
            }

        )


    except Exception as e:


        msg = str(e).lower()


        if (
            "response_format" in msg
            or
            "unsupported" in msg
            or
            "not supported" in msg
        ):

            raw = chat(

                messages,

                model=model,

                temperature=temperature,

                max_tokens=max_tokens

            )

        else:

            raise



    return _extract_json(raw)




# =====================================================
# JSON Extract
# =====================================================

def _extract_json(text:str):


    text=text.strip()


    try:

        return json.loads(text)

    except:

        pass



    if "```json" in text:

        start=text.index("```json")+7

        end=text.index(
            "```",
            start
        )

        return json.loads(
            text[start:end].strip()
        )



    for a,b in [
        ("{","}"),
        ("[","]")
    ]:

        if a in text:

            start=text.index(a)

            depth=0

            for i,ch in enumerate(
                text[start:],
                start
            ):

                if ch==a:
                    depth+=1

                elif ch==b:

                    depth-=1

                    if depth==0:

                        return json.loads(
                            text[start:i+1]
                        )


    raise ValueError(
        "Unable to parse the LLM JSON response"
    )