/**
 * Google Apps Script Webhook 接收端
 *
 * 功能：接收 Python 后端推送的 JSON Payload，解析并写入 Google Sheets。
 *
 * 部署步骤：
 *   1. 打开 Google Sheets，点击 扩展程序 → Apps Script
 *   2. 将本文件内容粘贴到 Code.gs 中
 *   3. 部署 → 新部署 → Web 应用 → 部署
 *   4. 复制生成的 Webhook URL，配置到 .env 的 GOOGLE_WEBHOOK_URL 中
 *
 * 注意：
 *   - deploy 时必须选择 "Anyone" 可访问（或至少 "Anyone with link"）
 *   - 如果修改了代码，需要重新部署（新版本）使更改生效
 */

// 目标工作表名称（与 Python 端对应）
var SHEET_NAME = "Daily Risk Dashboard";

/**
 * 处理 POST 请求
 */
function doPost(e) {
  try {
    // 解析 JSON Payload
    var payload = JSON.parse(e.postData.contents);

    // 获取或创建目标工作表
    var sheet = getOrCreateSheet(SHEET_NAME);

    // 提取日期和数据
    var date = payload.date || new Date().toISOString().split("T")[0];
    var metrics = payload.metrics || {};
    var alerts = payload.alerts || [];
    var crossAsset = payload.cross_asset || [];

    // 写入表头（如果工作表为空）
    if (sheet.getLastRow() === 0) {
      sheet.appendRow([
        "Date", "Ticker", "Skew_Spread", "IV_Put_25D", "IV_Call_25D",
        "Z_Score", "Alert_Flag", "Alert_Severity", "Alert_Direction"
      ]);
    }

    // 写入每个标的的数据
    var tickers = ["SPY", "QQQ", "IWM", "DIA"];
    for (var i = 0; i < tickers.length; i++) {
      var ticker = tickers[i];
      var data = metrics[ticker];

      if (data) {
        // 查找该标的的预警信息
        var alertInfo = null;
        for (var j = 0; j < alerts.length; j++) {
          if (alerts[j].ticker === ticker) {
            alertInfo = alerts[j];
            break;
          }
        }

        sheet.appendRow([
          date,
          ticker,
          data.skew_spread != null ? data.skew_spread : "",
          data.iv_put_25d != null ? data.iv_put_25d : "",
          data.iv_call_25d != null ? data.iv_call_25d : "",
          alertInfo ? alertInfo.z_score : "",
          alertInfo ? "YES" : "NO",
          alertInfo ? alertInfo.severity : "normal",
          alertInfo ? alertInfo.direction : ""
        ]);
      }
    }

    // 写入跨标的剪刀差
    for (var k = 0; k < crossAsset.length; k++) {
      var pairData = crossAsset[k];
      var pairName = pairData.pair ? pairData.pair.join("-") : "CROSS";

      sheet.appendRow([
        date,
        "CROSS:" + pairName,
        pairData.spread != null ? pairData.spread : "",
        "", "", "", "", "", ""
      ]);
    }

    // 返回成功响应
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "success",
        message: "成功写入 " + tickers.length + " 个标的 + 跨标的剪刀差",
        date: date
      }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    // 错误处理
    return ContentService
      .createTextOutput(JSON.stringify({
        status: "error",
        message: error.toString()
      }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * 处理 GET 请求（用于健康检查）
 */
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({
      status: "ok",
      service: "After-Hours Liquidity Monitor Webhook",
      version: "1.0.0",
      timestamp: new Date().toISOString()
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * 获取或创建工作表
 */
function getOrCreateSheet(sheetName) {
  var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();

  // 尝试获取现有工作表
  var sheet = spreadsheet.getSheetByName(sheetName);
  if (sheet) {
    return sheet;
  }

  // 创建新工作表
  sheet = spreadsheet.insertSheet(sheetName);
  return sheet;
}

/**
 * 清除工作表数据（测试用）
 */
function clearSheet() {
  var sheet = getOrCreateSheet(SHEET_NAME);
  sheet.clear();
  Logger.log("工作表已清除: " + SHEET_NAME);
}
