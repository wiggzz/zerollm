"use strict";

const crypto = require("node:crypto");
const {
  DynamoDBClient,
  GetItemCommand,
  QueryCommand,
  ScanCommand,
  UpdateItemCommand,
} = require("@aws-sdk/client-dynamodb");
const { InvokeCommand, LambdaClient } = require("@aws-sdk/client-lambda");

const VLLM_PORT = 8000;
const JSON_HEADERS = {
  "Content-Type": "application/json",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

const dynamodb = new DynamoDBClient({});
const lambda = new LambdaClient({});

function response(stream, statusCode, headers = JSON_HEADERS) {
  return awslambda.HttpResponseStream.from(stream, { statusCode, headers });
}

function writeJson(stream, statusCode, body, headers = {}) {
  const out = response(stream, statusCode, { ...JSON_HEADERS, ...headers });
  out.write(JSON.stringify(body));
  out.end();
}

function headerValue(headers, name) {
  if (!headers) return "";
  return headers[name] || headers[name.toLowerCase()] || "";
}

function bearerToken(event) {
  const auth = headerValue(event.headers, "authorization");
  return auth.startsWith("Bearer ") ? auth.slice("Bearer ".length) : "";
}

function hashApiKey(token) {
  return crypto.createHash("sha256").update(token, "utf8").digest("hex");
}

async function validateApiKey(token) {
  if (!token.startsWith("dio-")) return false;

  const keyHash = hashApiKey(token);
  const result = await dynamodb.send(
    new GetItemCommand({
      TableName: process.env.API_KEYS_TABLE,
      Key: { key_hash: { S: keyHash } },
      ProjectionExpression: "key_hash",
    })
  );

  const storedHash = result.Item?.key_hash?.S || "";
  if (!storedHash) return false;
  if (storedHash.length !== keyHash.length) return false;

  return crypto.timingSafeEqual(Buffer.from(storedHash), Buffer.from(keyHash));
}

async function listModels() {
  const result = await dynamodb.send(
    new ScanCommand({
      TableName: process.env.MODELS_TABLE,
      ProjectionExpression: "#name",
      ExpressionAttributeNames: { "#name": "name" },
    })
  );

  return {
    object: "list",
    data: (result.Items || []).map((item) => ({
      id: item.name.S,
      object: "model",
      owned_by: "diogenes",
    })),
  };
}

async function readyInstance(model) {
  return instanceByStatus(model, "ready");
}

async function startingInstance(model) {
  return instanceByStatus(model, "starting");
}

async function stoppedInstance(model) {
  return instanceByStatus(model, "stopped");
}

async function stoppingInstance(model) {
  return instanceByStatus(model, "stopping");
}

async function instanceByStatus(model, status) {
  const result = await dynamodb.send(
    new QueryCommand({
      TableName: process.env.INSTANCES_TABLE,
      IndexName: "model-status-index",
      KeyConditionExpression: "#model = :model AND #status = :status",
      ExpressionAttributeNames: {
        "#model": "model",
        "#status": "status",
      },
      ExpressionAttributeValues: {
        ":model": { S: model },
        ":status": { S: status },
      },
      Limit: 1,
    })
  );

  const item = result.Items?.[0];
  if (!item) return null;
  return {
    instanceId: item.instance_id.S,
    ip: item.ip?.S || "",
    previousIp: item.previous_ip?.S || "",
    stoppedAt: item.stopped_at?.N || "",
    warmExpiresAt: item.warm_expires_at?.N || "",
  };
}

async function triggerScaleUp(model) {
  await lambda.send(
    new InvokeCommand({
      FunctionName: process.env.ORCHESTRATOR_FUNCTION_NAME,
      InvocationType: "Event",
      Payload: Buffer.from(JSON.stringify({ action: "scale_up", model })),
    })
  );
}

async function beginRequest(instanceId) {
  const now = Math.floor(Date.now() / 1000);
  const token = `${now}:${crypto.randomUUID()}`;
  await dynamodb.send(
    new UpdateItemCommand({
      TableName: process.env.INSTANCES_TABLE,
      Key: { instance_id: { S: instanceId } },
      UpdateExpression:
        "SET #status = :busy, last_request_at = :now ADD active_request_starts :token",
      ExpressionAttributeNames: {
        "#status": "status",
      },
      ExpressionAttributeValues: {
        ":busy": { S: "busy" },
        ":now": { N: String(now) },
        ":token": { SS: [token] },
      },
    })
  );
  return token;
}

async function endRequest(instanceId, requestToken) {
  const result = await dynamodb.send(
    new UpdateItemCommand({
      TableName: process.env.INSTANCES_TABLE,
      Key: { instance_id: { S: instanceId } },
      UpdateExpression:
        "SET last_request_at = :now DELETE active_request_starts :token",
      ExpressionAttributeValues: {
        ":now": { N: String(Math.floor(Date.now() / 1000)) },
        ":token": { SS: [requestToken] },
      },
      ReturnValues: "ALL_NEW",
    })
  );

  const activeStarts = result.Attributes?.active_request_starts?.SS || [];
  if (activeStarts.length > 0) return;

  await dynamodb.send(
    new UpdateItemCommand({
      TableName: process.env.INSTANCES_TABLE,
      Key: { instance_id: { S: instanceId } },
      UpdateExpression:
        "SET #status = :ready, last_request_at = :now REMOVE active_request_starts",
      ExpressionAttributeNames: {
        "#status": "status",
      },
      ExpressionAttributeValues: {
        ":ready": { S: "ready" },
        ":now": { N: String(Math.floor(Date.now() / 1000)) },
      },
    })
  );
}

async function markInstanceReady(instanceId) {
  await dynamodb.send(
    new UpdateItemCommand({
      TableName: process.env.INSTANCES_TABLE,
      Key: { instance_id: { S: instanceId } },
      UpdateExpression: "SET #status = :status, last_request_at = :now",
      ExpressionAttributeNames: {
        "#status": "status",
      },
      ExpressionAttributeValues: {
        ":status": { S: "ready" },
        ":now": { N: String(Math.floor(Date.now() / 1000)) },
      },
    })
  );
}

async function healthyInstance(instance) {
  if (!instance?.ip) return false;

  const headers = {};
  if (process.env.VLLM_API_KEY) {
    headers.Authorization = `Bearer ${process.env.VLLM_API_KEY}`;
  }

  try {
    const health = await fetch(`http://${instance.ip}:${VLLM_PORT}/health`, {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(2500),
    });
    return health.status === 200;
  } catch {
    return false;
  }
}

async function routableInstance(model) {
  const ready = await readyInstance(model);
  if (ready) return ready;

  const busy = await instanceByStatus(model, "busy");
  if (busy) return busy;

  const starting = await startingInstance(model);
  if (await healthyInstance(starting)) {
    await markInstanceReady(starting.instanceId);
    return starting;
  }

  return null;
}

async function warmStartPending(model) {
  const starting = await startingInstance(model);
  if (starting?.previousIp || starting?.stoppedAt || starting?.warmExpiresAt) {
    return true;
  }

  if (await stoppedInstance(model)) {
    return true;
  }

  return Boolean(await stoppingInstance(model));
}

async function proxyStreaming(event, stream, path) {
  const bodyText = event.isBase64Encoded
    ? Buffer.from(event.body || "", "base64").toString("utf8")
    : event.body || "{}";
  const body = JSON.parse(bodyText);
  const model = body.model || "";

  if (!model) {
    writeJson(stream, 400, {
      error: { message: "model is required", type: "invalid_request_error" },
    });
    return;
  }

  const instance = await routableInstance(model);
  if (!instance) {
    const isWarmStart = await warmStartPending(model);
    await triggerScaleUp(model);
    writeJson(
      stream,
      503,
      {
        error: {
          message: isWarmStart
            ? "Model is warm-starting. Retry shortly."
            : "Model is cold-starting. Retry shortly.",
          type: "service_unavailable",
        },
      },
      { "Retry-After": "30" }
    );
    return;
  }

  const requestToken = await beginRequest(instance.instanceId);

  try {
    const upstreamHeaders = { "Content-Type": "application/json" };
    if (process.env.VLLM_API_KEY) {
      upstreamHeaders.Authorization = `Bearer ${process.env.VLLM_API_KEY}`;
    }

    const upstream = await fetch(`http://${instance.ip}:${VLLM_PORT}${path}`, {
      method: "POST",
      headers: upstreamHeaders,
      body: bodyText,
    });

    const contentType =
      upstream.headers.get("content-type") || "application/json";
    const out = response(stream, upstream.status, {
      ...JSON_HEADERS,
      "Content-Type": contentType,
    });

    if (!upstream.body) {
      out.end();
      return;
    }

    for await (const chunk of upstream.body) {
      out.write(chunk);
    }
    out.end();
  } finally {
    try {
      await endRequest(instance.instanceId, requestToken);
    } catch (err) {
      console.error("Failed to clear active request marker", err);
    }
  }
}

exports.handler = awslambda.streamifyResponse(async (event, stream) => {
  const method = event.requestContext?.http?.method || "GET";
  const path = event.rawPath || "/";

  try {
    if (method === "OPTIONS") {
      writeJson(stream, 200, {});
      return;
    }

    const token = bearerToken(event);
    if (!(await validateApiKey(token))) {
      writeJson(stream, 401, {
        error: { message: "unauthorized", type: "authentication_error" },
      });
      return;
    }

    if (method === "GET" && path === "/v1/models") {
      writeJson(stream, 200, await listModels());
      return;
    }

    if (
      method === "POST" &&
      ["/v1/messages", "/v1/responses", "/v1/chat/completions"].includes(path)
    ) {
      await proxyStreaming(event, stream, path);
      return;
    }

    writeJson(stream, 404, { error: "not found" });
  } catch (err) {
    console.error("Streaming router failed", err);
    writeJson(stream, 502, {
      error: {
        message: `Streaming router failed: ${err.message}`,
        type: "bad_gateway",
      },
    });
  }
});
