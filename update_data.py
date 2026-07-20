import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(TZ)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

STOCKS = [
    ("SZ.002460", "赣锋锂业", "0.002460", "sz002460"),
    ("SH.601600", "中国铝业", "1.601600", "sh601600"),
]


def to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_eastmoney(secid):
    """
    为兼容现有主程序，暂时保留原函数名。
    实际数据源顺序：新浪 -> 网易。
    """
    errors = []

    for fetcher in (fetch_sina, fetch_netease):
        try:
            return fetcher(secid)
        except Exception as error:
            errors.append(
                f"{fetcher.__name__}: {error}"
            )

    raise RuntimeError(
        "；".join(errors)
    )


def fetch_sina(secid):
    market, code = secid.split(".", 1)

    symbol = (
        ("sz" if market == "0" else "sh")
        + code
    )

    response = requests.get(
        f"https://hq.sinajs.cn/list={symbol}",
        headers={
            **HEADERS,
            "Referer": "https://finance.sina.com.cn/",
            "Accept": "text/plain,*/*",
        },
        timeout=15,
    )

    response.raise_for_status()

    text = response.content.decode(
        "gbk",
        errors="ignore",
    )

    match = re.search(
        r'="([^"]*)"',
        text,
    )

    if not match or not match.group(1):
        raise RuntimeError(
            "新浪返回空数据"
        )

    fields = match.group(1).split(",")

    if len(fields) < 32:
        raise RuntimeError(
            f"新浪字段不足: {len(fields)}"
        )

    open_price = to_number(fields[1])
    prev_close = to_number(fields[2])
    latest = to_number(fields[3])
    high = to_number(fields[4])
    low = to_number(fields[5])
    volume = to_number(fields[8])
    turnover = to_number(fields[9])

    quote_date = fields[30].replace("/", "-")
    quote_time = fields[31]

    timestamp = (
        f"{quote_date} {quote_time}"
        if quote_date and quote_time
        else None
    )

    change = (
        round(latest - prev_close, 4)
        if latest is not None
        and prev_close is not None
        else None
    )

    pct_change = (
        round(
            change / prev_close * 100,
            4,
        )
        if change is not None
        and prev_close
        else None
    )

    amplitude = (
        round(
            (high - low) / prev_close * 100,
            4,
        )
        if high is not None
        and low is not None
        and prev_close
        else None
    )

    return {
        "source": "sina",
        "timestamp": timestamp,
        "latest": latest,
        "prevClose": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": volume,
        "turnover": turnover,
        "change": change,
        "pctChange": pct_change,
        "amplitude": amplitude,
    }


def fetch_netease(secid):
    market, code = secid.split(".", 1)

    netease_code = (
        ("1" if market == "0" else "0")
        + code
    )

    response = requests.get(
        (
            "http://api.money.126.net/data/feed/"
            f"{netease_code},money.api"
        ),
        headers={
            **HEADERS,
            "Referer": "http://quotes.money.163.com/",
        },
        timeout=15,
    )

    response.raise_for_status()

    text = response.content.decode(
        "utf-8",
        errors="ignore",
    )

    match = re.search(
        r"_ntes_quote_callback\((.*)\);",
        text,
        re.S,
    )

    if not match:
        raise RuntimeError(
            "网易返回格式无法解析"
        )

    payload = json.loads(
        match.group(1)
    )

    item = payload.get(netease_code)

    if not item:
        raise RuntimeError(
            "网易返回空数据"
        )

    latest = to_number(
        item.get("price")
    )
    prev_close = to_number(
        item.get("yestclose")
    )
    open_price = to_number(
        item.get("open")
    )
    high = to_number(
        item.get("high")
    )
    low = to_number(
        item.get("low")
    )

    timestamp = normalize_quote_time(
        item.get("update")
        or item.get("time")
    )

    change = (
        round(latest - prev_close, 4)
        if latest is not None
        and prev_close is not None
        else None
    )

    pct_change = (
        round(
            change / prev_close * 100,
            4,
        )
        if change is not None
        and prev_close
        else None
    )

    amplitude = (
        round(
            (high - low) / prev_close * 100,
            4,
        )
        if high is not None
        and low is not None
        and prev_close
        else None
    )

    return {
        "source": "netease",
        "timestamp": timestamp,
        "latest": latest,
        "prevClose": prev_close,
        "open": open_price,
        "high": high,
        "low": low,
        "volume": to_number(
            item.get("volume")
        ),
        "turnover": to_number(
            item.get("turnover")
        ),
        "change": change,
        "pctChange": pct_change,
        "amplitude": amplitude,
    }


def normalize_quote_time(value):
    if value is None:
        return None

    text = str(value).strip().replace(
        "/",
        "-",
    )

    if not text:
        return None

    if re.fullmatch(r"\d{14}", text):
        return datetime.strptime(
            text,
            "%Y%m%d%H%M%S",
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    if re.fullmatch(
        r"\d{2}:\d{2}:\d{2}",
        text,
    ):
        return (
            NOW.strftime("%Y-%m-%d")
            + " "
            + text
        )

    if len(text) >= 19:
        return text[:19]

    return text

def fetch_tencent():
    symbols = ",".join(stock[3] for stock in STOCKS)

    response = requests.get(
        (
            f"https://qt.gtimg.cn/q={symbols}"
            f"&_={int(NOW.timestamp() * 1000)}"
        ),
        headers={
            **HEADERS,
            "Referer": "https://gu.qq.com/",
        },
        timeout=15,
    )

    response.raise_for_status()

    text = response.content.decode(
        "gbk",
        errors="ignore",
    )

    output = {}

    matches = re.findall(
        r'v_((?:sz|sh)\d{6})="([^"]*)"',
        text,
    )

    for symbol, body in matches:
        fields = body.split("~")

        if len(fields) < 39:
            continue

        code = (
            f"{symbol[:2].upper()}."
            f"{symbol[2:]}"
        )

        timestamp = None
        raw_time = fields[30]

        if re.fullmatch(r"\d{14}", raw_time):
            timestamp = (
                datetime.strptime(
                    raw_time,
                    "%Y%m%d%H%M%S",
                )
                .replace(tzinfo=TZ)
                .strftime("%Y-%m-%d %H:%M:%S")
            )

        amount = to_number(fields[37])

        output[code] = {
            "source": "tencent",
            "timestamp": timestamp,
            "latest": to_number(fields[3]),
            "prevClose": to_number(fields[4]),
            "open": to_number(fields[5]),
            "high": to_number(fields[33]),
            "low": to_number(fields[34]),
            "volume": to_number(fields[36]),
            "turnover": (
                amount * 10000
                if amount is not None
                else None
            ),
            "turnoverRate": to_number(fields[38]),
        }

    if not output:
        raise RuntimeError("腾讯行情无法解析")

    return output


def price_difference(value_a, value_b):
    if value_a is None or value_b is None:
        return None

    return round(
        abs(value_a - value_b),
        6,
    )


def verify_quotes(eastmoney, tencent):
    errors = []

    limits = {
        "latest": 0.05,
        "prevClose": 0.02,
        "open": 0.02,
        "high": 0.05,
        "low": 0.05,
    }

    differences = {}

    for field, limit in limits.items():
        differences[field] = price_difference(
            eastmoney.get(field),
            tencent.get(field),
        )

        if (
            differences[field] is None
            or differences[field] > limit
        ):
            errors.append(
                f"{field}_mismatch"
            )

    eastmoney_time = eastmoney.get("timestamp")
    tencent_time = tencent.get("timestamp")

    if not eastmoney_time or not tencent_time:
        errors.append("timestamp_missing")

    elif eastmoney_time[:10] != tencent_time[:10]:
        errors.append("trading_date_mismatch")

    for source_name, quote in (
        ("eastmoney", eastmoney),
        ("tencent", tencent),
    ):
        latest = quote.get("latest")
        open_price = quote.get("open")
        high = quote.get("high")
        low = quote.get("low")

        required = [
            latest,
            open_price,
            high,
            low,
        ]

        if any(value is None for value in required):
            errors.append(
                f"{source_name}_required_field_missing"
            )
            continue

        if high < max(latest, open_price):
            errors.append(
                f"{source_name}_high_logic_error"
            )

        if low > min(latest, open_price):
            errors.append(
                f"{source_name}_low_logic_error"
            )

    return {
        "passed": not errors,
        "errors": errors,
        "differences": differences,
    }


def main():
    result = {
        "generatedAt": NOW.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "timezone": "Asia/Shanghai",
        "status": "行情核准失败",
        "allowTradeAnalysis": False,
        "stocks": {},
        "errors": [],
    }

    try:
        tencent_quotes = fetch_tencent()

    except Exception as error:
        tencent_quotes = {}

        result["errors"].append(
            f"tencent_error: {error}"
        )

    for code, name, secid, _ in STOCKS:
        try:
            eastmoney_quote = fetch_eastmoney(
                secid
            )

        except Exception as error:
            eastmoney_quote = None

            result["errors"].append(
                f"{code}_eastmoney_error: {error}"
            )

        tencent_quote = tencent_quotes.get(code)

        if eastmoney_quote and tencent_quote:
            verification = verify_quotes(
                eastmoney_quote,
                tencent_quote,
            )

        else:
            verification = {
                "passed": False,
                "errors": ["source_missing"],
                "differences": {},
            }

        result["stocks"][code] = {
            "code": code,
            "name": name,
            "eastmoney": eastmoney_quote,
            "tencent": tencent_quote,
            "verification": verification,
        }

    result["allowTradeAnalysis"] = all(
        stock["verification"]["passed"]
        for stock in result["stocks"].values()
    )

    result["status"] = (
        "行情核准成功"
        if result["allowTradeAnalysis"]
        else "行情核准失败"
    )

    Path("latest.json").write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
