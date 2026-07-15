// Risk Register tools for the shifter-ops MCP server.

import { z } from "zod";
import { registerTool } from "../policy.js";
import { ok, err } from "../respond.js";
import { RISK_TABLES, buildUpdateSet } from "../lib.js";
import {
  EnvSchema,
  SeveritySchema,
  StatusSchema,
  StrideSchema,
  ScoreSchema,
} from "../schemas.js";

export function registerRiskTools(ctx, deps) {
  const { withClient } = deps;

  registerTool(ctx, {
    name: "list_risks",
    klass: "named_db_read",
    description:
      "List risk register entries. Returns active (non-deleted) risks by default, with computed risk_score and comment_count. Use filters to narrow results.",
    schema: {
      status: StatusSchema.optional().describe("Filter by lifecycle status"),
      severity: SeveritySchema.optional().describe("Filter by severity level"),
      include_deleted: z
        .boolean()
        .default(false)
        .describe("Include soft-deleted risks (default: false)"),
      env: EnvSchema,
    },
    handler: async ({ status, severity, include_deleted, env }) => {
      try {
        return await withClient(env, { readOnly: true }, async (client) => {
          const conditions = [];
          const params = [];
          let paramIdx = 1;

          if (!include_deleted) {
            conditions.push("r.deleted_at IS NULL");
          }
          if (status) {
            conditions.push(`r.status = $${paramIdx}`);
            params.push(status);
            paramIdx++;
          }
          if (severity) {
            conditions.push(`r.severity = $${paramIdx}`);
            params.push(severity);
            paramIdx++;
          }

          const where =
            conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

          const result = await client.query(
            `SELECT r.id, r.title, r.severity, r.status,
                    r.stride_categories, r.likelihood_score, r.impact_score,
                    r.likelihood_score * r.impact_score AS risk_score,
                    r.created_at, r.updated_at, r.deleted_at,
                    (SELECT COUNT(*) FROM ${RISK_TABLES.comment} c
                     WHERE c.risk_id = r.id AND c.deleted_at IS NULL) AS comment_count
             FROM ${RISK_TABLES.risk} r
             ${where}
             ORDER BY r.created_at DESC`,
            params
          );

          return ok(
            JSON.stringify(
              { count: result.rowCount, risks: result.rows },
              null,
              2
            )
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "get_risk",
    klass: "named_db_read",
    description:
      "Get a single risk by ID with full details, including all comments and recent audit history.",
    schema: {
      risk_id: z.number().int().positive().describe("Risk ID"),
      env: EnvSchema,
    },
    handler: async ({ risk_id, env }) => {
      try {
        return await withClient(env, { readOnly: true }, async (client) => {
          const riskResult = await client.query(
            `SELECT r.*,
                    r.likelihood_score * r.impact_score AS risk_score
             FROM ${RISK_TABLES.risk} r
             WHERE r.id = $1`,
            [risk_id]
          );

          if (riskResult.rows.length === 0) {
            return {
              content: [{ type: "text", text: `Risk ${risk_id} not found.` }],
              isError: true,
            };
          }

          const commentsResult = await client.query(
            `SELECT c.id, c.content, c.parent_comment_id, c.created_at,
                    COALESCE(u.email, 'API: ' || ak.name, 'Unknown') AS author
             FROM ${RISK_TABLES.comment} c
             LEFT JOIN auth_user u ON c.author_user_id = u.id
             LEFT JOIN ${RISK_TABLES.apikey} ak ON c.author_apikey_id = ak.id
             WHERE c.risk_id = $1 AND c.deleted_at IS NULL
             ORDER BY c.created_at`,
            [risk_id]
          );

          const auditResult = await client.query(
            `SELECT action, timestamp, previous_state, new_state, context
             FROM ${RISK_TABLES.audit_log}
             WHERE entity_type = 'risk' AND entity_id = $1
             ORDER BY timestamp DESC
             LIMIT 20`,
            [risk_id]
          );

          return ok(
            JSON.stringify(
              {
                risk: riskResult.rows[0],
                comments: commentsResult.rows,
                audit_log: auditResult.rows,
              },
              null,
              2
            )
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "create_risk",
    klass: "named_db_write",
    description:
      "Create a new risk register entry. Only title and description are required; all other fields have sensible defaults.",
    schema: {
      title: z.string().min(1).max(200).describe("Short title for the risk"),
      description: z.string().min(1).describe("Detailed risk description"),
      severity: SeveritySchema.default("medium").describe(
        "Severity level (default: medium)"
      ),
      status: StatusSchema.default("open").describe(
        "Initial status (default: open)"
      ),
      stride_categories: StrideSchema.default([]).describe(
        "STRIDE threat categories (default: none)"
      ),
      likelihood_score: ScoreSchema.nullable()
        .default(null)
        .describe("Likelihood score 1-5 (optional)"),
      impact_score: ScoreSchema.nullable()
        .default(null)
        .describe("Impact score 1-5 (optional)"),
      attack_vector: z
        .string()
        .default("")
        .describe("How the threat could be exploited (optional)"),
      affected_assets: z
        .string()
        .default("")
        .describe("What systems/assets are affected (optional)"),
      mitigation_status: z
        .string()
        .default("")
        .describe("Current mitigation efforts (optional)"),
      env: EnvSchema,
    },
    handler: async ({
      title,
      description,
      severity,
      status,
      stride_categories,
      likelihood_score,
      impact_score,
      attack_vector,
      affected_assets,
      mitigation_status,
      env,
    }) => {
      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          const result = await client.query(
            `INSERT INTO ${RISK_TABLES.risk}
               (title, description, severity, status, stride_categories,
                likelihood_score, impact_score, attack_vector, affected_assets,
                mitigation_status, resolution_reason, created_at, updated_at)
             VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, '', NOW(), NOW())
             RETURNING *,
               likelihood_score * impact_score AS risk_score`,
            [
              title,
              description,
              severity,
              status,
              JSON.stringify(stride_categories),
              likelihood_score,
              impact_score,
              attack_vector,
              affected_assets,
              mitigation_status,
            ]
          );

          return ok(
            JSON.stringify({ created: true, risk: result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "update_risk",
    klass: "named_db_write",
    description:
      "Update one or more fields on an existing risk. Only provide the fields you want to change. Returns the full updated risk.",
    schema: {
      risk_id: z.number().int().positive().describe("Risk ID to update"),
      title: z
        .string()
        .min(1)
        .max(200)
        .optional()
        .describe("New title (optional)"),
      description: z
        .string()
        .min(1)
        .optional()
        .describe("New description (optional)"),
      severity: SeveritySchema.optional().describe("New severity (optional)"),
      status: StatusSchema.optional().describe("New status (optional)"),
      stride_categories: StrideSchema.optional().describe(
        "New STRIDE categories (optional)"
      ),
      likelihood_score: ScoreSchema.nullable()
        .optional()
        .describe("New likelihood score 1-5, or null to clear (optional)"),
      impact_score: ScoreSchema.nullable()
        .optional()
        .describe("New impact score 1-5, or null to clear (optional)"),
      attack_vector: z
        .string()
        .optional()
        .describe("New attack vector (optional)"),
      affected_assets: z
        .string()
        .optional()
        .describe("New affected assets (optional)"),
      mitigation_status: z
        .string()
        .optional()
        .describe("New mitigation status (optional)"),
      resolution_reason: z
        .string()
        .optional()
        .describe("Reason for resolution/closure (optional)"),
      env: EnvSchema,
    },
    handler: async ({
      risk_id,
      title,
      description,
      severity,
      status,
      stride_categories,
      likelihood_score,
      impact_score,
      attack_vector,
      affected_assets,
      mitigation_status,
      resolution_reason,
      env,
    }) => {
      const fieldMap = {
        title,
        description,
        severity,
        status,
        likelihood_score,
        impact_score,
        attack_vector,
        affected_assets,
        mitigation_status,
        resolution_reason,
      };

      if (stride_categories !== undefined) {
        fieldMap.stride_categories = JSON.stringify(stride_categories);
      }

      let updateInfo;
      try {
        updateInfo = buildUpdateSet(fieldMap);
      } catch {
        return {
          content: [
            {
              type: "text",
              text: "Error: No fields provided to update. Provide at least one field to change.",
            },
          ],
          isError: true,
        };
      }

      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          let setClause = `${updateInfo.setClause}, updated_at = NOW()`;
          if (stride_categories !== undefined) {
            setClause = setClause.replace(
              /stride_categories = \$(\d+)/,
              (_, n) => `stride_categories = $${n}::jsonb`
            );
          }

          const result = await client.query(
            `UPDATE ${RISK_TABLES.risk}
             SET ${setClause}
             WHERE id = $${updateInfo.nextParam} AND deleted_at IS NULL
             RETURNING *, likelihood_score * impact_score AS risk_score`,
            [...updateInfo.values, risk_id]
          );

          if (result.rows.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `Risk ${risk_id} not found or is deleted.`,
                },
              ],
              isError: true,
            };
          }

          return ok(
            JSON.stringify({ updated: true, risk: result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "delete_risk",
    klass: "named_db_write",
    description:
      "Soft-delete a risk (sets deleted_at timestamp). The risk can be restored later with restore_risk.",
    schema: {
      risk_id: z.number().int().positive().describe("Risk ID to soft-delete"),
      env: EnvSchema,
    },
    handler: async ({ risk_id, env }) => {
      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          const result = await client.query(
            `UPDATE ${RISK_TABLES.risk}
             SET deleted_at = NOW(), updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL
             RETURNING id, title`,
            [risk_id]
          );

          if (result.rows.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `Risk ${risk_id} not found or already deleted.`,
                },
              ],
              isError: true,
            };
          }

          return ok(
            JSON.stringify({ deleted: true, ...result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "restore_risk",
    klass: "named_db_write",
    description: "Restore a soft-deleted risk (clears deleted_at timestamp).",
    schema: {
      risk_id: z
        .number()
        .int()
        .positive()
        .describe("Risk ID to restore from soft-delete"),
      env: EnvSchema,
    },
    handler: async ({ risk_id, env }) => {
      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          const result = await client.query(
            `UPDATE ${RISK_TABLES.risk}
             SET deleted_at = NULL, updated_at = NOW()
             WHERE id = $1 AND deleted_at IS NOT NULL
             RETURNING id, title`,
            [risk_id]
          );

          if (result.rows.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `Risk ${risk_id} not found or is not deleted.`,
                },
              ],
              isError: true,
            };
          }

          return ok(
            JSON.stringify({ restored: true, ...result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "add_risk_comment",
    klass: "named_db_write",
    description: "Add a comment to a risk. Comments are immutable once created.",
    schema: {
      risk_id: z
        .number()
        .int()
        .positive()
        .describe("Risk ID to comment on"),
      content: z.string().min(1).describe("Comment text"),
      env: EnvSchema,
    },
    handler: async ({ risk_id, content, env }) => {
      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          const riskCheck = await client.query(
            `SELECT id FROM ${RISK_TABLES.risk} WHERE id = $1 AND deleted_at IS NULL`,
            [risk_id]
          );

          if (riskCheck.rows.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `Risk ${risk_id} not found or is deleted.`,
                },
              ],
              isError: true,
            };
          }

          const result = await client.query(
            `INSERT INTO ${RISK_TABLES.comment} (risk_id, content, created_at)
             VALUES ($1, $2, NOW())
             RETURNING id, risk_id, content, created_at`,
            [risk_id, content]
          );

          return ok(
            JSON.stringify({ created: true, comment: result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "delete_risk_comment",
    klass: "named_db_write",
    description: "Soft-delete a comment on a risk (sets deleted_at timestamp).",
    schema: {
      comment_id: z.number().int().positive().describe("Comment ID to delete"),
      env: EnvSchema,
    },
    handler: async ({ comment_id, env }) => {
      try {
        return await withClient(env, { readOnly: false }, async (client) => {
          const result = await client.query(
            `UPDATE ${RISK_TABLES.comment}
             SET deleted_at = NOW()
             WHERE id = $1 AND deleted_at IS NULL
             RETURNING id, risk_id`,
            [comment_id]
          );

          if (result.rows.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: `Comment ${comment_id} not found or already deleted.`,
                },
              ],
              isError: true,
            };
          }

          return ok(
            JSON.stringify({ deleted: true, ...result.rows[0] }, null, 2)
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "risk_dashboard",
    klass: "observability",
    description:
      "Get a summary dashboard of the risk register: total counts, breakdown by severity and status, top risks by score, and recent activity.",
    schema: {
      env: EnvSchema,
    },
    handler: async ({ env }) => {
      try {
        return await withClient(env, { readOnly: true }, async (client) => {
          const totals = await client.query(
            `SELECT
               COUNT(*) FILTER (WHERE deleted_at IS NULL) AS active_risks,
               COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) AS deleted_risks
             FROM ${RISK_TABLES.risk}`
          );

          const bySeverity = await client.query(
            `SELECT severity, COUNT(*) AS count
             FROM ${RISK_TABLES.risk}
             WHERE deleted_at IS NULL
             GROUP BY severity
             ORDER BY CASE severity
               WHEN 'critical' THEN 1 WHEN 'high' THEN 2
               WHEN 'medium' THEN 3 WHEN 'low' THEN 4 END`
          );

          const byStatus = await client.query(
            `SELECT status, COUNT(*) AS count
             FROM ${RISK_TABLES.risk}
             WHERE deleted_at IS NULL
             GROUP BY status
             ORDER BY CASE status
               WHEN 'open' THEN 1 WHEN 'acknowledged' THEN 2
               WHEN 'mitigating' THEN 3 WHEN 'resolved' THEN 4
               WHEN 'closed' THEN 5 END`
          );

          const topRisks = await client.query(
            `SELECT id, title, severity, status,
                    likelihood_score, impact_score,
                    likelihood_score * impact_score AS risk_score
             FROM ${RISK_TABLES.risk}
             WHERE deleted_at IS NULL
               AND likelihood_score IS NOT NULL
               AND impact_score IS NOT NULL
             ORDER BY risk_score DESC, created_at DESC
             LIMIT 10`
          );

          const recentAudit = await client.query(
            `SELECT al.action, al.entity_type, al.entity_id,
                    al.timestamp, al.context
             FROM ${RISK_TABLES.audit_log} al
             ORDER BY al.timestamp DESC
             LIMIT 10`
          );

          return ok(
            JSON.stringify(
              {
                totals: totals.rows[0],
                by_severity: bySeverity.rows,
                by_status: byStatus.rows,
                top_risks_by_score: topRisks.rows,
                recent_activity: recentAudit.rows,
              },
              null,
              2
            )
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "risk_matrix",
    klass: "observability",
    description:
      "Get a 5x5 risk matrix (likelihood vs impact). Each cell shows the count of risks and their titles. Useful for visualizing risk distribution.",
    schema: {
      env: EnvSchema,
    },
    handler: async ({ env }) => {
      try {
        return await withClient(env, { readOnly: true }, async (client) => {
          const result = await client.query(
            `SELECT likelihood_score, impact_score,
                    COUNT(*) AS count,
                    json_agg(json_build_object(
                      'id', id, 'title', title, 'severity', severity
                    ) ORDER BY id) AS risks
             FROM ${RISK_TABLES.risk}
             WHERE deleted_at IS NULL
               AND likelihood_score IS NOT NULL
               AND impact_score IS NOT NULL
             GROUP BY likelihood_score, impact_score
             ORDER BY likelihood_score DESC, impact_score DESC`
          );

          const matrix = {};
          for (let l = 1; l <= 5; l++) {
            matrix[l] = {};
            for (let i = 1; i <= 5; i++) {
              matrix[l][i] = { count: 0, score: l * i, risks: [] };
            }
          }
          for (const row of result.rows) {
            matrix[row.likelihood_score][row.impact_score] = {
              count: Number(row.count),
              score: row.likelihood_score * row.impact_score,
              risks: row.risks,
            };
          }

          return ok(
            JSON.stringify(
              {
                description:
                  "5x5 risk matrix. Outer key = likelihood (1-5), inner key = impact (1-5). Score = likelihood × impact.",
                matrix,
              },
              null,
              2
            )
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });

  registerTool(ctx, {
    name: "risk_audit_log",
    klass: "named_db_read",
    description:
      "Get the audit history for a specific risk, showing all state changes with timestamps, actions, and before/after state snapshots.",
    schema: {
      risk_id: z
        .number()
        .int()
        .positive()
        .describe("Risk ID to get audit history for"),
      limit: z
        .number()
        .int()
        .min(1)
        .max(100)
        .default(50)
        .describe("Max entries to return (default: 50, max: 100)"),
      env: EnvSchema,
    },
    handler: async ({ risk_id, limit, env }) => {
      try {
        return await withClient(env, { readOnly: true }, async (client) => {
          const result = await client.query(
            `SELECT action, actor_type, actor_id,
                    timestamp, previous_state, new_state, context
             FROM ${RISK_TABLES.audit_log}
             WHERE entity_type = 'risk' AND entity_id = $1
             ORDER BY timestamp DESC
             LIMIT $2`,
            [risk_id, limit]
          );

          return ok(
            JSON.stringify(
              {
                risk_id,
                entry_count: result.rowCount,
                entries: result.rows,
              },
              null,
              2
            )
          );
        });
      } catch (e) {
        return err(e);
      }
    },
  });
}
