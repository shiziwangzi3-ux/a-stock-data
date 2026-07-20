import json
import math
from datetime import datetime
from pathlib import Path
from statistics import pstdev
from zoneinfo import ZoneInfo

import requests


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(TZ)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

STOCKS = [
    ("SZ.002460", "赣锋锂业", "sz002460"),
    ("SH.601600", "中国铝业", "sh601600"),
]

TIMEFRAMES = {
    "15m": {
        "sinaScale": 15,
        "tencentKey": "m15",
        "count": 240,
    },
    "60m": {
        "sinaScale": 60,
        "tencentKey": "m60",
        "count": 240,
    },
    "1d": {
        "sinaScale": 240,
        "tencentKey": "day",
        "count": 260,
    },
}


def to_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def round_or_none(value, digits=6):
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def normalize_timestamp(value):
    text = str(value or "").strip().replace("/", "-")
    if not text:
        return None

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
        "%Y-%m-%d",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                return parsed.strftime("%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    return text[:19]


def normalize_bars(rows):
    output = {}

    for row in rows:
        timestamp = normalize_timestamp(row.get("timestamp"))
        open_price = to_float(row.get("open"))
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        close = to_float(row.get("close"))
        volume = to_float(row.get("volume"))

        if (
            not timestamp
            or any(
                value is None
                for value in (
                    open_price,
                    high,
                    low,
                    close,
                    volume,
                )
            )
        ):
            continue

        if high < max(open_price, close) or low > min(open_price, close):
            continue

        output[timestamp] = {
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return [output[key] for key in sorted(output)]


def fetch_sina_kline(symbol, scale, count):
    response = requests.get(
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        "CN_MarketDataService.getKLineData",
        params={
            "symbol": symbol,
            "scale": scale,
            "ma": "no",
            "datalen": count,
        },
        headers={
            **HEADERS,
            "Referer": "https://finance.sina.com.cn/",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list):
        raise RuntimeError("新浪K线返回格式异常")

    bars = normalize_bars(
        {
            "timestamp": item.get("day"),
            "open": item.get("open"),
            "high": item.get("high"),
            "low": item.get("low"),
            "close": item.get("close"),
            "volume": item.get("volume"),
        }
        for item in payload
        if isinstance(item, dict)
    )

    if len(bars) < 60:
        raise RuntimeError(f"新浪K线数量不足: {len(bars)}")

    return bars


def find_tencent_rows(container, key):
    preferred_keys = (
        key,
        f"qfq{key}",
        "qfqday" if key == "day" else "",
        "day" if key == "day" else "",
    )

    for candidate in preferred_keys:
        if candidate and isinstance(container.get(candidate), list):
            return container[candidate]

    for candidate, value in container.items():
        if isinstance(value, list) and candidate.endswith(key):
            return value

    return None


def fetch_tencent_kline(symbol, key, count):
    response = requests.get(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={
            "param": f"{symbol},{key},,,{count},qfq",
        },
        headers={
            **HEADERS,
            "Referer": "https://gu.qq.com/",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    stock_data = (
        payload.get("data", {}).get(symbol)
        if isinstance(payload, dict)
        else None
    )

    if not isinstance(stock_data, dict):
        raise RuntimeError("腾讯K线返回空数据")

    rows = find_tencent_rows(stock_data, key)

    if not rows:
        raise RuntimeError("腾讯K线字段缺失")

    parsed_rows = []

    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue

        parsed_rows.append(
            {
                "timestamp": row[0],
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
            }
        )

    bars = normalize_bars(parsed_rows)

    if len(bars) < 60:
        raise RuntimeError(f"腾讯K线数量不足: {len(bars)}")

    return bars


def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values, period):
    if not values:
        return []

    multiplier = 2 / (period + 1)
    output = [values[0]]

    for value in values[1:]:
        output.append(
            value * multiplier
            + output[-1] * (1 - multiplier)
        )

    return output


def calculate_rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[index] - values[index - 1]
        for index in range(1, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for index in range(period, len(changes)):
        average_gain = (
            average_gain * (period - 1)
            + gains[index]
        ) / period
        average_loss = (
            average_loss * (period - 1)
            + losses[index]
        ) / period

    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0

    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def calculate_macd(values):
    if len(values) < 35:
        return None

    ema12 = ema_series(values, 12)
    ema26 = ema_series(values, 26)
    dif = [
        fast - slow
        for fast, slow in zip(ema12, ema26)
    ]
    dea = ema_series(dif, 9)
    histogram = [
        2 * (dif_value - dea_value)
        for dif_value, dea_value in zip(dif, dea)
    ]

    current = {
        "dif": round_or_none(dif[-1]),
        "dea": round_or_none(dea[-1]),
        "histogram": round_or_none(histogram[-1]),
    }
    previous = {
        "dif": round_or_none(dif[-2]),
        "dea": round_or_none(dea[-2]),
        "histogram": round_or_none(histogram[-2]),
    }

    if dif[-1] > dea[-1] and histogram[-1] > 0:
        state = "多头"
    elif dif[-1] < dea[-1] and histogram[-1] < 0:
        state = "空头"
    else:
        state = "收敛"

    current["state"] = state
    current["histogramDirection"] = (
        "扩大"
        if abs(histogram[-1]) > abs(histogram[-2])
        else "缩小"
    )
    current["previous"] = previous
    return current


def calculate_boll(values, period=20, multiplier=2):
    if len(values) < period:
        return None

    window = values[-period:]
    middle = sum(window) / period
    deviation = pstdev(window)
    upper = middle + multiplier * deviation
    lower = middle - multiplier * deviation
    close = values[-1]

    if close > upper:
        position = "上轨之外"
    elif close < lower:
        position = "下轨之外"
    elif close >= middle:
        position = "中轨上方"
    else:
        position = "中轨下方"

    return {
        "upper": round_or_none(upper),
        "middle": round_or_none(middle),
        "lower": round_or_none(lower),
        "position": position,
        "bandwidthPct": round_or_none(
            (upper - lower) / middle * 100
            if middle
            else None
        ),
    }


def calculate_ma(values):
    output = {}

    for period in (5, 10, 20, 60):
        current = sma(values, period)
        previous = (
            sum(values[-period - 1:-1]) / period
            if len(values) > period
            else None
        )

        output[f"ma{period}"] = {
            "value": round_or_none(current),
            "direction": (
                "向上"
                if current is not None
                and previous is not None
                and current > previous
                else "向下"
                if current is not None
                and previous is not None
                and current < previous
                else "走平"
            ),
        }

    return output


def calculate_volume(bars):
    volumes = [bar["volume"] for bar in bars]
    latest = volumes[-1]
    average5 = sma(volumes, 5)
    average20 = sma(volumes, 20)

    return {
        "latest": round_or_none(latest, 2),
        "average5": round_or_none(average5, 2),
        "average20": round_or_none(average20, 2),
        "ratioToAverage5": round_or_none(
            latest / average5 if average5 else None,
            4,
        ),
        "ratioToAverage20": round_or_none(
            latest / average20 if average20 else None,
            4,
        ),
    }


def derive_state(close, ma, macd, rsi):
    score = 0

    ma5 = ma["ma5"]["value"]
    ma10 = ma["ma10"]["value"]
    ma20 = ma["ma20"]["value"]

    if ma20 is not None:
        score += 1 if close > ma20 else -1

    if None not in (ma5, ma10, ma20):
        if ma5 > ma10 > ma20:
            score += 1
        elif ma5 < ma10 < ma20:
            score -= 1

    if macd:
        score += 1 if macd["histogram"] > 0 else -1

    if rsi is not None:
        if rsi >= 55:
            score += 1
        elif rsi <= 45:
            score -= 1

    if score >= 3:
        label = "偏强"
    elif score <= -3:
        label = "偏弱"
    else:
        label = "震荡"

    return {
        "label": label,
        "score": score,
        "note": "仅为指标状态汇总，不直接等同于买卖指令",
    }


def analyze_bars(bars):
    closes = [bar["close"] for bar in bars]
    ma = calculate_ma(closes)
    macd = calculate_macd(closes)
    rsi = calculate_rsi(closes)
    boll = calculate_boll(closes)
    latest = bars[-1]

    return {
        "barsCount": len(bars),
        "latestBar": {
            "timestamp": latest["timestamp"],
            "open": round_or_none(latest["open"]),
            "high": round_or_none(latest["high"]),
            "low": round_or_none(latest["low"]),
            "close": round_or_none(latest["close"]),
            "volume": round_or_none(latest["volume"], 2),
        },
        "ma": ma,
        "macd": macd,
        "rsi14": round_or_none(rsi, 4),
        "boll20": boll,
        "volume": calculate_volume(bars),
        "technicalState": derive_state(
            latest["close"],
            ma,
            macd,
            rsi,
        ),
    }


def load_latest_quotes():
    path = Path("latest.json")

    if not path.exists():
        raise RuntimeError("latest.json不存在")

    payload = json.loads(path.read_text(encoding="utf-8"))

    return payload


def quote_from_latest(payload, code):
    stock = payload.get("stocks", {}).get(code, {})
    source = stock.get("tencent") or stock.get("eastmoney") or {}

    return {
        "price": to_float(source.get("latest")),
        "timestamp": normalize_timestamp(source.get("timestamp")),
        "verificationPassed": bool(
            stock.get("verification", {}).get("passed")
        ),
    }


def comparison(selected_bars, other_bars, latest_quote):
    selected_close = selected_bars[-1]["close"]
    selected_time = selected_bars[-1]["timestamp"]
    quote_price = latest_quote.get("price")
    quote_time = latest_quote.get("timestamp")

    quote_difference = (
        abs(selected_close - quote_price)
        if quote_price is not None
        else None
    )
    quote_difference_pct = (
        quote_difference / quote_price * 100
        if quote_difference is not None and quote_price
        else None
    )

    cross_difference = None
    cross_difference_pct = None
    other_close = None
    other_time = None

    if other_bars:
        other_close = other_bars[-1]["close"]
        other_time = other_bars[-1]["timestamp"]
        cross_difference = abs(selected_close - other_close)
        cross_difference_pct = (
            cross_difference / selected_close * 100
            if selected_close
            else None
        )

    same_trading_date = (
        bool(selected_time and quote_time)
        and selected_time[:10] == quote_time[:10]
    )

    passed = (
        latest_quote.get("verificationPassed")
        and same_trading_date
        and quote_difference_pct is not None
        and quote_difference_pct <= 1.5
    )

    return {
        "passed": passed,
        "sameTradingDate": same_trading_date,
        "latestQuote": {
            "price": round_or_none(quote_price),
            "timestamp": quote_time,
        },
        "selectedLastBar": {
            "close": round_or_none(selected_close),
            "timestamp": selected_time,
        },
        "latestQuoteDifference": round_or_none(
            quote_difference
        ),
        "latestQuoteDifferencePct": round_or_none(
            quote_difference_pct,
            4,
        ),
        "crossSource": {
            "otherClose": round_or_none(other_close),
            "otherTimestamp": other_time,
            "difference": round_or_none(cross_difference),
            "differencePct": round_or_none(
                cross_difference_pct,
                4,
            ),
            "passed": (
                cross_difference_pct is not None
                and cross_difference_pct <= 0.5
            ),
        },
    }


def choose_source(source_bars, latest_quote):
    candidates = [
        (source_name, bars)
        for source_name, bars in source_bars.items()
        if bars and len(bars) >= 60
    ]

    if not candidates:
        raise RuntimeError("无可用K线数据源")

    quote_price = latest_quote.get("price")

    if quote_price is None:
        return candidates[0]

    return min(
        candidates,
        key=lambda item: abs(
            item[1][-1]["close"] - quote_price
        ),
    )


def build_timeframe(symbol, timeframe, config, latest_quote):
    source_bars = {}
    errors = []

    try:
        source_bars["sina"] = fetch_sina_kline(
            symbol,
            config["sinaScale"],
            config["count"],
        )
    except Exception as error:
        errors.append(f"sina: {error}")

    try:
        source_bars["tencent"] = fetch_tencent_kline(
            symbol,
            config["tencentKey"],
            config["count"],
        )
    except Exception as error:
        errors.append(f"tencent: {error}")

    selected_source, selected_bars = choose_source(
        source_bars,
        latest_quote,
    )
    other_bars = next(
        (
            bars
            for source_name, bars in source_bars.items()
            if source_name != selected_source
        ),
        None,
    )

    verification = comparison(
        selected_bars,
        other_bars,
        latest_quote,
    )

    return {
        "timeframe": timeframe,
        "selectedSource": selected_source,
        "availableSources": sorted(source_bars),
        "sourceErrors": errors,
        "verification": verification,
        "analysis": analyze_bars(selected_bars),
    }


def main():
    result = {
        "generatedAt": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "status": "技术指标生成失败",
        "allowTechnicalAnalysis": False,
        "stocks": {},
        "errors": [],
    }

    try:
        latest_payload = load_latest_quotes()
    except Exception as error:
        latest_payload = {}
        result["errors"].append(f"latest_json_error: {error}")

    for code, name, symbol in STOCKS:
        latest_quote = quote_from_latest(
            latest_payload,
            code,
        )
        stock_result = {
            "code": code,
            "name": name,
            "latestQuote": latest_quote,
            "timeframes": {},
            "errors": [],
        }

        for timeframe, config in TIMEFRAMES.items():
            try:
                stock_result["timeframes"][timeframe] = (
                    build_timeframe(
                        symbol,
                        timeframe,
                        config,
                        latest_quote,
                    )
                )
            except Exception as error:
                stock_result["errors"].append(
                    f"{timeframe}: {error}"
                )

        stock_result["allowTechnicalAnalysis"] = (
            len(stock_result["timeframes"]) == len(TIMEFRAMES)
            and all(
                item["verification"]["passed"]
                for item in stock_result["timeframes"].values()
            )
        )
        result["stocks"][code] = stock_result

    result["allowTechnicalAnalysis"] = (
        bool(result["stocks"])
        and all(
            stock["allowTechnicalAnalysis"]
            for stock in result["stocks"].values()
        )
    )
    result["status"] = (
        "技术指标生成成功"
        if result["allowTechnicalAnalysis"]
        else "技术指标生成部分成功"
        if any(
            stock["timeframes"]
            for stock in result["stocks"].values()
        )
        else "技术指标生成失败"
    )

    Path("technical.json").write_text(
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
