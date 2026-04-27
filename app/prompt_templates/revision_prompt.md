PROMPT_STAGE: revision

你正在规划修订阶段。用户认为当前 PLAN.md 不符合要求，请根据反馈只修订规划文档。

【项目目标 / 题目】
{{PROJECT_GOAL}}

【用户反馈】
{{FEEDBACK}}

【严格限制】
1. 只允许修改 PLAN.md、HANDOFF.md、TEST_REPORT.md。
2. 不得进入代码开发，不得创建业务代码，不得执行数据库/后端/前端实现任务。
3. 不得把执行项标记为 - [x]；规划阶段所有执行 checkbox 应保持 - [ ]。
4. 必须继续遵守固定约束：Java 21、Spring Boot 3.x、MyBatis-Plus、Vue3 + Element Plus、本地 MySQL root/123456、不使用 Redis、本地文件存储、顶层目录仅 frontend/、backend/、db/。
5. auth-only 只能作为基础权限模板或复用来源，禁止作为最终项目名、业务目录名或业务模块名。
6. 修订后最终回复只给 PLAN.md 的关键变化与待确认点，等待用户点击“开始执行”。

【修订要求】
- 根据用户反馈调整模块拆分、接口、页面、数据表、权限、校验、异常、测试和执行步骤。
- 如果用户反馈与现有规划冲突，以用户反馈优先，但必须在 PLAN.md 的待确认项或风险中说明影响。
- 继续保持 PLAN.md 是可直接执行的开发作战图：无空字段、无占位符、无“同上”、无不可验收任务。
- 同步维护 HANDOFF.md，记录当前仍处于“等待开始”的规划状态。
- 同步维护 TEST_REPORT.md，说明本轮是规划修订，测试未执行及原因。

【当前 PLAN.md】
```markdown
{{PLAN_MD}}
```

【当前 HANDOFF.md】
```markdown
{{HANDOFF_MD}}
```

【当前 TEST_REPORT.md】
```markdown
{{TEST_REPORT_MD}}
```

【上传约束文件】
{{CONSTRAINTS}}
