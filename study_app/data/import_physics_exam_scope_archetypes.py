from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from study_app.data.database import DEFAULT_DB_PATH, connect, dumps, ensure_seeded_database
from study_app.data.practice_repository import upsert_practice_source


def U(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


SUBJECT = U(r"\u5927\u5b66\u7269\u7406\u5b66")
SOURCE_TITLE = U(r"\u5927\u5b66\u7269\u7406\u7b2c9-14\u7ae0\u671f\u672b\u8303\u56f4\u9898\u578b\u79cd\u5b50 v1")
SOURCE_NOTE = U(
    r"\u9488\u5bf9\u5927\u5b66\u7269\u7406\u671f\u672b\u8303\u56f4\uff089-14\u7ae0\uff09\u7684\u9898\u5e93\u8584\u5f31\u70b9\u8fdb\u884c\u8865\u8db3\uff1b"
    r"\u4ec5\u7528\u4e8e\u9898\u578b\u3001\u8003\u70b9\u4e0e\u96be\u5ea6\u6821\u51c6\uff0c\u4e0d\u76f4\u63a5\u590d\u5236\u539f\u9898\u3002"
)


TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    ("PHYS-TEMP-EOS", U(r"\u6e29\u5ea6\u4e0e\u72b6\u6001\u65b9\u7a0b"), U(r"\u6e29\u5ea6\u3001\u6e29\u6807\u3001\u7406\u60f3/\u975e\u7406\u60f3\u6c14\u4f53\u72b6\u6001\u65b9\u7a0b\u3001\u70ed\u81a8\u80c0\u7cfb\u6570\u4e0e\u7b49\u6e29\u538b\u7f29\u7cfb\u6570"), "chapter10"),
    ("PHYS-FIRST-LAW", U(r"\u70ed\u529b\u5b66\u7b2c\u4e00\u5b9a\u5f8b"), U(r"\u70ed\u91cf\u3001\u529f\u3001\u5185\u80fd\u3001p-V \u8fc7\u7a0b\u3001\u70ed\u5bb9\u4e0e\u7edd\u70ed\u8fc7\u7a0b"), "chapter11"),
    ("PHYS-SECOND-LAW-ENGINE", U(r"\u7b2c\u4e8c\u5b9a\u5f8b\u4e0e\u70ed\u673a"), U(r"Carnot \u5b9a\u7406\u3001\u70ed\u673a/\u5236\u51b7\u673a\u6548\u7387\u3001Clausius \u4e0d\u7b49\u5f0f\u3001\u71b5\u4e0e\u6700\u5927\u529f"), "chapter12"),
    ("PHYS-THERMO-POTENTIAL", U(r"\u70ed\u529b\u5b66\u52bf\u4e0e Maxwell \u5173\u7cfb"), U(r"\u81ea\u7531\u80fd\u3001Gibbs/Helmholtz \u52bf\u3001Maxwell \u5173\u7cfb\u3001TdS \u65b9\u7a0b\u4e0e\u504f\u5bfc\u6052\u7b49\u5f0f"), "chapter12"),
    ("PHYS-STATISTICAL", U(r"\u7406\u60f3\u6c14\u4f53\u5fae\u89c2\u6a21\u578b"), U(r"\u6c14\u4f53\u52a8\u7406\u8bba\u3001Maxwell/Boltzmann \u5206\u5e03\u3001\u901f\u7387\u5e73\u5747\u4e0e\u80fd\u91cf\u5747\u5206"), "chapter13"),
    ("PHYS-PHASE-TRANSITION", U(r"\u76f8\u5e73\u8861\u4e0e\u76f8\u53d8"), U(r"\u5316\u5b66\u52bf\u3001\u76f8\u5e73\u8861\u6761\u4ef6\u3001\u76f8\u56fe\u3001Clapeyron \u65b9\u7a0b\u3001\u4e00\u7ea7/\u4e8c\u7ea7\u76f8\u53d8"), "chapter14"),
)


COMMON_ERRORS = [
    U(r"\u6df7\u6dc6\u9002\u7528\u6761\u4ef6\u548c\u53d8\u91cf\u4fdd\u6301\u4e0d\u53d8\u7684\u8fc7\u7a0b"),
    U(r"\u7b26\u53f7\u7ea6\u5b9a\u9519\u8bef\uff0c\u5c24\u5176\u662f\u7cfb\u7edf\u5bf9\u5916\u505a\u529f\u4e0e\u5916\u754c\u5bf9\u7cfb\u7edf\u505a\u529f"),
    U(r"\u628a\u5b9a\u6027\u5224\u65ad\u9898\u5f53\u6210\u5957\u516c\u5f0f\u8ba1\u7b97\u9898"),
]


def problem(
    template_id: str,
    title: str,
    topic: str,
    difficulty: int,
    points: list[str],
    statement: str,
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "title": U(title),
        "topic": U(topic),
        "difficulty": difficulty,
        "tested_points": [U(item) for item in points],
        "statement": U(statement),
    }


PROBLEMS: tuple[dict[str, Any], ...] = (
    problem("PHYS-TEMP-EOS", r"\u975e\u7406\u60f3\u72b6\u6001\u65b9\u7a0b\u4e0e Boyle \u6e29\u5ea6", r"\u7406\u60f3\u6c14\u4f53\u72b6\u6001\u65b9\u7a0b", 70, [r"\u72b6\u6001\u65b9\u7a0b", r"\u538b\u7f29\u56e0\u5b50", r"Boyle \u6e29\u5ea6"], r"\u7ed9\u51fa van der Waals \u578b\u72b6\u6001\u65b9\u7a0b\uff0c\u6c42\u4f4e\u538b\u8fd1\u4f3c\u4e0b\u7684\u538b\u7f29\u56e0\u5b50\u5e76\u5224\u65ad Boyle \u6e29\u5ea6\u3002"),
    problem("PHYS-TEMP-EOS", r"\u7531\u72b6\u6001\u65b9\u7a0b\u6c42\u81a8\u80c0\u7cfb\u6570\u4e0e\u538b\u7f29\u7cfb\u6570", r"\u81a8\u80c0\u7cfb\u6570\u4e0e\u538b\u7f29\u7cfb\u6570", 78, [r"\u504f\u5bfc\u8ba1\u7b97", r"\u54cd\u5e94\u7cfb\u6570", r"\u72b6\u6001\u65b9\u7a0b"], r"\u5df2\u77e5 p=A T^3/V \u6216\u7c7b\u4f3c\u72b6\u6001\u65b9\u7a0b\uff0c\u6c42\u70ed\u81a8\u80c0\u7cfb\u6570\u548c\u7b49\u6e29\u538b\u7f29\u7cfb\u6570\uff0c\u5e76\u68c0\u9a8c\u7ef4\u5ea6\u3002"),
    problem("PHYS-TEMP-EOS", r"\u6df7\u5408\u6c14\u4f53\u6e29\u6807\u4e0e\u538b\u529b\u6821\u51c6", r"\u6e29\u5ea6\u4e0e\u6e29\u6807", 62, [r"\u6e29\u6807", r"\u7406\u60f3\u6c14\u4f53", r"\u5b9e\u9a8c\u6570\u636e\u5904\u7406"], r"\u7ed9\u51fa\u5b9a\u5bb9\u6c14\u4f53\u6e29\u5ea6\u8ba1\u5728\u4e24\u4e2a\u56fa\u5b9a\u70b9\u7684\u538b\u5f3a\u8bfb\u6570\uff0c\u6c42\u5f85\u6d4b\u72b6\u6001\u6e29\u5ea6\u5e76\u8bf4\u660e\u7ebf\u6027\u63d2\u503c\u524d\u63d0\u3002"),
    problem("PHYS-TEMP-EOS", r"\u54cd\u5e94\u7cfb\u6570\u95f4\u7684\u6052\u7b49\u5173\u7cfb\u8bc1\u660e", r"\u81a8\u80c0\u7cfb\u6570\u4e0e\u538b\u7f29\u7cfb\u6570", 82, [r"\u5faa\u73af\u5173\u7cfb", r"\u504f\u5bfc\u6052\u7b49\u5f0f", r"\u54cd\u5e94\u7cfb\u6570"], r"\u4ece p,V,T \u7684\u72b6\u6001\u65b9\u7a0b\u51fa\u53d1\uff0c\u8bc1\u660e\u70ed\u81a8\u80c0\u7cfb\u6570\u3001\u7b49\u6e29\u538b\u7f29\u7cfb\u6570\u548c\u5b9a\u5bb9\u538b\u529b\u6e29\u5ea6\u7cfb\u6570\u7684\u5173\u7cfb\u3002"),
    problem("PHYS-FIRST-LAW", r"p-V \u5206\u6bb5\u8def\u5f84\u7684\u529f\u3001\u70ed\u91cf\u4e0e\u5185\u80fd", r"\u70ed\u529b\u5b66\u7b2c\u4e00\u5b9a\u5f8b", 72, [r"p-V \u56fe", r"\u529f\u7684\u7b26\u53f7", r"\u7b2c\u4e00\u5b9a\u5f8b"], r"\u7406\u60f3\u6c14\u4f53\u7ecf\u8fc7\u7b49\u538b\u3001\u7b49\u5bb9\u548c\u76f4\u7ebf p-V \u8def\u5f84\u7ec4\u6210\u7684\u8fc7\u7a0b\uff0c\u6c42\u5404\u6bb5\u505a\u529f\u3001\u5185\u80fd\u6539\u53d8\u548c\u5438\u653e\u70ed\u3002"),
    problem("PHYS-FIRST-LAW", r"\u7edd\u70ed\u8fc7\u7a0b\u4e0e\u70ed\u5bb9\u6bd4\u7684\u8054\u5408\u8ba1\u7b97", r"\u70ed\u5bb9\u4e0e\u7edd\u70ed\u8fc7\u7a0b", 78, [r"\u7edd\u70ed\u65b9\u7a0b", r"\u70ed\u5bb9\u6bd4", r"\u8fc7\u7a0b\u91cf"], r"\u4e00\u5b9a\u91cf\u7406\u60f3\u6c14\u4f53\u7ecf\u7edd\u70ed\u538b\u7f29\u540e\u518d\u7b49\u5bb9\u5347\u6e29\uff0c\u6c42\u672b\u6001\u53c2\u91cf\u3001\u5404\u6bb5\u529f\u548c\u70ed\u91cf\u3002"),
    problem("PHYS-FIRST-LAW", r"\u7b80\u5316 Joule \u5faa\u73af\u6216 Otto \u5faa\u73af\u6548\u7387", r"\u7406\u60f3\u6c14\u4f53\u8fc7\u7a0b", 80, [r"\u5faa\u73af", r"\u7edd\u70ed\u8fc7\u7a0b", r"\u6548\u7387"], r"\u6309 p-V \u56fe\u7ed9\u51fa\u7406\u60f3\u6c14\u4f53\u5faa\u73af\uff0c\u8981\u6c42\u7528\u4f53\u79ef\u6bd4\u8868\u793a\u70ed\u673a\u6548\u7387\u5e76\u6807\u51fa\u5438\u70ed\u548c\u653e\u70ed\u6bb5\u3002"),
    problem("PHYS-FIRST-LAW", r"\u975e\u51c6\u9759\u6001\u8fc7\u7a0b\u7684\u7b2c\u4e00\u5b9a\u5f8b\u5224\u65ad", r"\u70ed\u91cf\u3001\u529f\u4e0e\u5185\u80fd", 68, [r"\u975e\u51c6\u9759\u6001", r"\u5185\u80fd", r"\u673a\u68b0\u529f"], r"\u94c1\u7403\u843d\u4e0b\u5f39\u8d77\u6216\u6c14\u4f53\u81ea\u7531\u81a8\u80c0\u7c7b\u95ee\u9898\uff0c\u5206\u6790\u662f\u5426\u6709\u70ed\u4f20\u9012\u3001\u662f\u5426\u5bf9\u5916\u505a\u529f\u548c\u5185\u80fd\u5982\u4f55\u53d8\u5316\u3002"),
    problem("PHYS-SECOND-LAW-ENGINE", r"Carnot \u70ed\u673a\u4e0e\u5236\u51b7\u673a\u7684\u8054\u5408\u6548\u7387", r"\u70ed\u529b\u5b66\u7b2c\u4e8c\u5b9a\u5f8b\u4e0e Carnot \u5b9a\u7406", 72, [r"Carnot \u5b9a\u7406", r"\u6548\u7387", r"COP"], r"\u4e24\u70ed\u6e90\u4e4b\u95f4\u540c\u65f6\u8fd0\u884c\u70ed\u673a\u548c\u5236\u51b7\u673a\uff0c\u6c42\u6548\u7387\u3001\u5236\u51b7\u7cfb\u6570\u548c\u603b\u71b5\u53d8\u3002"),
    problem("PHYS-SECOND-LAW-ENGINE", r"\u4e0d\u53ef\u9006\u81ea\u7531\u81a8\u80c0\u4e0e\u53ef\u9006\u590d\u539f", r"\u71b5\u4e0e\u71b5\u589e\u539f\u7406", 76, [r"\u71b5\u53d8", r"\u4e0d\u53ef\u9006\u8fc7\u7a0b", r"\u53ef\u9006\u8def\u5f84"], r"\u7406\u60f3\u6c14\u4f53\u81ea\u7531\u81a8\u80c0\u5230\u4e24\u500d\u4f53\u79ef\uff0c\u6c42\u7cfb\u7edf\u71b5\u53d8\u3001\u73af\u5883\u71b5\u53d8\uff0c\u5e76\u8bbe\u8ba1\u4e00\u6761\u53ef\u9006\u590d\u539f\u8def\u5f84\u3002"),
    problem("PHYS-SECOND-LAW-ENGINE", r"\u6709\u9650\u70ed\u6e90\u4e4b\u95f4\u7684\u6700\u5927\u53ef\u7528\u529f", r"\u6700\u5927\u529f\u539f\u7406", 84, [r"\u6700\u5927\u529f", r"\u71b5\u5b88\u6052\u6761\u4ef6", r"\u6709\u9650\u70ed\u6e90"], r"\u4e24\u4e2a\u70ed\u5bb9\u76f8\u540c\u3001\u521d\u6e29\u4e0d\u540c\u7684\u7269\u4f53\u901a\u8fc7\u53ef\u9006\u70ed\u673a\u4ea4\u6362\u70ed\u91cf\uff0c\u6c42\u6700\u7ec8\u5e73\u8861\u6e29\u5ea6\u548c\u53ef\u8f93\u51fa\u6700\u5927\u529f\u3002"),
    problem("PHYS-THERMO-POTENTIAL", r"\u7531 Helmholtz \u81ea\u7531\u80fd\u5bfc\u51fa\u72b6\u6001\u65b9\u7a0b", r"Helmholtz \u4e0e Gibbs \u81ea\u7531\u80fd", 76, [r"\u81ea\u7136\u53d8\u91cf", r"\u72b6\u6001\u65b9\u7a0b", r"\u504f\u5bfc"], r"\u7ed9\u51fa F(T,V) \u7684\u8868\u8fbe\u5f0f\uff0c\u6c42 p\u3001S\u3001U \u5e76\u5224\u65ad\u7a33\u5b9a\u6027\u6761\u4ef6\u3002"),
    problem("PHYS-THERMO-POTENTIAL", r"TdS \u65b9\u7a0b\u4e0e\u54cd\u5e94\u7cfb\u6570\u7ed3\u5408", r"Maxwell \u5173\u7cfb\u4e0e TdS \u65b9\u7a0b", 82, [r"TdS \u65b9\u7a0b", r"\u70ed\u5bb9", r"\u54cd\u5e94\u7cfb\u6570"], r"\u4ece dU=T dS-p dV \u548c Maxwell \u5173\u7cfb\u51fa\u53d1\uff0c\u63a8\u5bfc dS \u7528 (T,V) \u6216 (T,p) \u8868\u793a\u7684\u5f62\u5f0f\u3002"),
    problem("PHYS-THERMO-POTENTIAL", r"\u5316\u5b66\u52bf\u76f8\u7b49\u4e0e Gibbs-Duhem \u7ea6\u675f", r"\u76f8\u5e73\u8861\u6761\u4ef6\u4e0e\u5316\u5b66\u52bf", 84, [r"\u5316\u5b66\u52bf", r"\u76f8\u5e73\u8861", r"Gibbs-Duhem"], r"\u4e24\u76f8\u5355\u5143\u7cfb\u5171\u5b58\u65f6\uff0c\u7528\u5316\u5b66\u52bf\u76f8\u7b49\u63a8\u5bfc\u76f8\u5e73\u8861\u66f2\u7ebf\u7684\u5fae\u5206\u6761\u4ef6\u3002"),
    problem("PHYS-STATISTICAL", r"\u5206\u5b50\u78b0\u58c1\u6a21\u578b\u63a8\u5bfc\u538b\u5f3a", r"\u72b6\u6001\u65b9\u7a0b\u7684\u5fae\u89c2\u89e3\u91ca", 68, [r"\u5fae\u89c2\u6a21\u578b", r"\u538b\u5f3a", r"\u5e73\u5747\u52a8\u80fd"], r"\u7531\u7acb\u65b9\u5bb9\u5668\u4e2d\u5206\u5b50\u5f39\u6027\u78b0\u58c1\u6a21\u578b\u63a8\u5bfc pV=(2/3)N\u5e73\u5747\u5e73\u52a8\u52a8\u80fd\uff0c\u5e76\u8bf4\u660e\u7edf\u8ba1\u5e73\u5747\u7684\u6765\u6e90\u3002"),
    problem("PHYS-STATISTICAL", r"Maxwell \u901f\u7387\u5206\u5e03\u4e09\u79cd\u7279\u5f81\u901f\u7387", r"\u7406\u60f3\u6c14\u4f53\u5fae\u89c2\u6a21\u578b", 74, [r"\u6700\u6982\u7136\u901f\u7387", r"\u5e73\u5747\u901f\u7387", r"\u65b9\u5747\u6839\u901f\u7387"], r"\u7ed9\u51fa Maxwell \u901f\u7387\u5206\u5e03\uff0c\u6c42\u6700\u6982\u7136\u901f\u7387\u3001\u5e73\u5747\u901f\u7387\u548c\u65b9\u5747\u6839\u901f\u7387\u7684\u8868\u8fbe\u5f0f\u5e76\u6bd4\u8f83\u5927\u5c0f\u3002"),
    problem("PHYS-STATISTICAL", r"\u80fd\u91cf\u5747\u5206\u4e0e\u70ed\u5bb9\u7684\u5206\u5b50\u81ea\u7531\u5ea6\u5224\u65ad", r"\u5b8f\u89c2\u91cf\u4e0e\u5fae\u89c2\u91cf\u8054\u7cfb", 76, [r"\u80fd\u91cf\u5747\u5206", r"\u81ea\u7531\u5ea6", r"\u70ed\u5bb9"], r"\u7ed9\u51fa\u5355\u539f\u5b50\u3001\u53cc\u539f\u5b50\u6216\u5177\u6709\u632f\u52a8\u81ea\u7531\u5ea6\u7684\u6c14\u4f53\uff0c\u6c42 C_V,C_p \u548c gamma \u7684\u7406\u8bba\u503c\u3002"),
    problem("PHYS-STATISTICAL", r"\u9009\u901a\u91cf\u6216\u675f\u6d41\u7684\u901f\u7387\u5206\u5e03\u504f\u7f6e", r"\u7406\u60f3\u6c14\u4f53\u5fae\u89c2\u6a21\u578b", 82, [r"\u901f\u7387\u5206\u5e03", r"\u901a\u91cf\u52a0\u6743", r"\u5e73\u5747\u503c"], r"\u6c14\u4f53\u901a\u8fc7\u5c0f\u5b54\u6cc4\u51fa\u5f62\u6210\u5206\u5b50\u675f\uff0c\u6bd4\u8f83\u675f\u6d41\u4e2d\u7684\u901f\u7387\u5206\u5e03\u4e0e\u5bb9\u5668\u5185 Maxwell \u5206\u5e03\u7684\u5e73\u5747\u901f\u7387\u5dee\u5f02\u3002"),
    problem("PHYS-PHASE-TRANSITION", r"Clausius-Clapeyron \u65b9\u7a0b\u4e0e\u84b8\u6c14\u538b\u66f2\u7ebf", r"\u76f8\u56fe\u4e0e Clapeyron \u65b9\u7a0b", 76, [r"Clapeyron \u65b9\u7a0b", r"\u6f5c\u70ed", r"\u84b8\u6c14\u538b"], r"\u5047\u8bbe\u6c14\u76f8\u8fd1\u4f3c\u4e3a\u7406\u60f3\u6c14\u4f53\u4e14\u6f5c\u70ed\u8fd1\u4f3c\u4e3a\u5e38\u91cf\uff0c\u7531 Clapeyron \u65b9\u7a0b\u63a8\u5bfc\u84b8\u6c14\u538b\u968f\u6e29\u5ea6\u7684\u5173\u7cfb\u3002"),
    problem("PHYS-PHASE-TRANSITION", r"\u76f8\u56fe\u659c\u7387\u7b26\u53f7\u4e0e\u6f5c\u70ed\u5224\u65ad", r"\u76f8\u56fe\u4e0e Clapeyron \u65b9\u7a0b", 72, [r"\u76f8\u56fe", r"\u4f53\u79ef\u53d8\u5316", r"\u6f5c\u70ed\u7b26\u53f7"], r"\u7ed9\u51fa p-T \u76f8\u56fe\u4e2d\u4e00\u6761\u76f8\u8f6c\u53d8\u66f2\u7ebf\u7684\u659c\u7387\uff0c\u5224\u65ad\u4e24\u76f8\u4f53\u79ef\u53d8\u5316\u548c\u5438\u653e\u70ed\u65b9\u5411\u3002"),
    problem("PHYS-PHASE-TRANSITION", r"\u4e00\u7ea7\u4e0e\u4e8c\u7ea7\u76f8\u53d8\u7684 Gibbs \u51fd\u6570\u5224\u522b", r"\u4e00\u7ea7\u76f8\u53d8\u4e0e\u4e8c\u7ea7\u76f8\u53d8", 78, [r"Gibbs \u51fd\u6570", r"\u4e00\u9636\u5bfc\u6570", r"\u4e8c\u9636\u5bfc\u6570"], r"\u7ed9\u51fa\u4e24\u76f8\u7684 G(T,p) \u66f2\u7ebf\u6216\u5bfc\u6570\u4fe1\u606f\uff0c\u5224\u65ad\u76f8\u53d8\u7c7b\u578b\u5e76\u8bf4\u660e\u71b5\u3001\u4f53\u79ef\u3001\u70ed\u5bb9\u7b49\u91cf\u7684\u8fde\u7eed\u6027\u3002"),
    problem("PHYS-PHASE-TRANSITION", r"\u5355\u5143\u7cfb\u76f8\u5f8b\u4e0e\u4e09\u76f8\u70b9\u81ea\u7531\u5ea6", r"\u76f8\u5e73\u8861\u6761\u4ef6\u4e0e\u5316\u5b66\u52bf", 80, [r"\u76f8\u5f8b", r"\u5316\u5b66\u52bf\u76f8\u7b49", r"\u4e09\u76f8\u70b9"], r"\u5bf9\u5355\u5143\u7cfb\u4e2d\u4e00\u76f8\u3001\u4e24\u76f8\u3001\u4e09\u76f8\u5171\u5b58\u7684\u60c5\u51b5\uff0c\u5199\u51fa\u5e73\u8861\u6761\u4ef6\u5e76\u7528\u76f8\u5f8b\u5224\u65ad\u81ea\u7531\u5ea6\u3002"),
)


def import_physics_exam_scope_archetypes(db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, int]:
    path = ensure_seeded_database(db_path)
    with connect(path) as connection:
        source_id = upsert_practice_source(connection, "course_archetype", SOURCE_TITLE, "", SOURCE_NOTE)
        for template_id, title, description, chapter in TEMPLATES:
            connection.execute(
                """
                INSERT INTO practice_templates(
                    template_id, subject_hint, topic_hint, title, description,
                    generation_rules_json, source_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id) DO UPDATE SET
                    subject_hint=excluded.subject_hint,
                    topic_hint=excluded.topic_hint,
                    title=excluded.title,
                    description=excluded.description,
                    generation_rules_json=excluded.generation_rules_json,
                    source_json=excluded.source_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    template_id,
                    SUBJECT,
                    title,
                    template_id,
                    description,
                    dumps({"style": "physics_exam_scope_archetype", "exam_chapter": chapter}),
                    dumps({"kind": "built_in_physics_exam_scope_archetype_v1"}),
                ),
            )
        for item in PROBLEMS:
            connection.execute(
                """
                INSERT INTO practice_problems(
                    template_id, title, statement, answer_outline,
                    common_errors_json, difficulty_score, difficulty_source,
                    subject_hint, topic_hint, tags_json, source_id, source_note,
                    raw_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(template_id, title) DO UPDATE SET
                    statement=excluded.statement,
                    answer_outline=excluded.answer_outline,
                    common_errors_json=excluded.common_errors_json,
                    difficulty_score=excluded.difficulty_score,
                    difficulty_source=excluded.difficulty_source,
                    subject_hint=excluded.subject_hint,
                    topic_hint=excluded.topic_hint,
                    tags_json=excluded.tags_json,
                    source_id=excluded.source_id,
                    source_note=excluded.source_note,
                    raw_json=excluded.raw_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item["template_id"],
                    item["title"],
                    item["statement"],
                    U(r"\u9898\u578b\u79cd\u5b50\u7528\u4e8e\u7ea6\u675f\u7ed3\u6784\u3001\u8003\u70b9\u548c\u96be\u5ea6\uff1b\u5177\u4f53\u53d8\u5f0f\u9898\u9700\u5355\u72ec\u63a8\u5bfc\u4e0e\u6821\u9a8c\u7b54\u6848\u3002"),
                    dumps(COMMON_ERRORS),
                    item["difficulty"],
                    "course_archetype_physics_exam_scope",
                    SUBJECT,
                    item["topic"],
                    dumps(item["tested_points"] + [item["template_id"], "physics_exam_scope"]),
                    source_id,
                    SOURCE_NOTE,
                    dumps(item),
                ),
            )
    return {
        "templates": len(TEMPLATES),
        "problems": len(PROBLEMS),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    print(json.dumps(import_physics_exam_scope_archetypes(), ensure_ascii=False, indent=2))
