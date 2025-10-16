# server.py
import asyncio
from datetime import datetime
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Context

# Create an MCP server with debug enabled
mcp = FastMCP("Demo", debug=True)


@mcp.tool()
def get_current_time() -> str:
    """現在時刻を取得する

    Returns:
        str: 現在時刻
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@mcp.tool()
async def long_sleep(minutes: int, ctx: Context) -> str:
    """長時間、待つ。

    Args:
        minutes: 待つ時間（分）
        ctx: コンテキスト

    Returns:
        str: 終わったよ！

    None:
        場合によってはタイムアウトする可能性がある。そのため、long_sleepを呼び出す前に、get_current_timeを呼び出して、現在時刻を取得しておくことを推奨する。
    """
    REPORT_INTERVAL = 30  # 60秒ごとに進捗を報告
    MAX_COUNT = minutes * 60 // REPORT_INTERVAL
    for i in range(MAX_COUNT):
        await ctx.report_progress(i, MAX_COUNT)
        await asyncio.sleep(REPORT_INTERVAL)
    return "終わったよ！"


# Add this part to run the server
if __name__ == "__main__":
    # stdioトランスポートを使用
    print("Starting MCP server in stdio mode")
    mcp.run(transport="stdio")
