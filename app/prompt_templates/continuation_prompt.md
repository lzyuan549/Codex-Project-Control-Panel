PROMPT_STAGE: execution

继续上一轮开发任务，请严格执行。

【项目目标 / 题目】
{{PROJECT_GOAL}}

【本轮选中的未完成项】
本轮最多只推进下面 10 个未完成项。不要故意处理后续未选中的任务，除非为了完成当前项需要很小的辅助改动。
{{SELECTED_BATCH}}

【执行规则】
1. 先读取 ./PLAN.md、./HANDOFF.md、./TEST_REPORT.md。
2. 用不超过 8 行总结：目标、已完成、未完成、风险、立即下一步。
3. 不得重置、删除、改写已勾选项（- [x]）。
4. 从本轮选中的未完成项继续执行；如果发现当前计划粒度不足，先补细 PLAN.md，再继续。
5. 每完成一项：
   - 先更新 PLAN.md 打勾并写完成说明 + 时间（YYYY-MM-DD HH:mm +08:00）。
   - 再更新 TEST_REPORT.md（如涉及测试/检查）。
   - 再更新 HANDOFF.md。
   - 最后回复进度。
6. 每次回复固定包含：
   - 已完成：第N项（名称）。
   - 进行中：第M项（名称）。
   - 下一项：第K项（名称）。
   - 风险/阻塞：无或具体说明。
7. 测试数据使用可以直接使用的中文真实数据，而不是英文模块名、test、demo 等占位数据。
8. 如果判断上下文即将接近上限，先更新 PLAN.md、HANDOFF.md、TEST_REPORT.md，再输出“可续接提示词”。
9. 不允许虚构执行结果；未执行就写“未执行 + 原因”。
10. 运行验证命令时禁止把长期服务留在前台；如需启动 dev server/后端服务，只能用于短时验证，验证完成后必须停止相关进程并在 TEST_REPORT.md 记录命令与结果。

【固定约束】
- 后端：Java 21，Spring Boot 3.x，MyBatis-Plus。
- 前端：后台管理端 Vue3 + Element Plus；用户端为微信小程序或 Vue3，按 PLAN.md 已确认方案执行。
- 后台布局以 PLAN.md 已确认方案为准。
- 顶层目录仅允许 frontend/、backend/、db/。
- 数据库为本地 MySQL（root / 123456）。
- SQL 全部写在一个文件。
- 不使用 Redis。
- 文件仅存本地相对路径，并保持静态资源映射可访问。
- 前端文案全中文。
- 继续优先复用现有模块、接口、表结构、权限与命名风格。
- auth-only 只能作为基础权限模板或复用来源，不得作为最终项目名、业务目录名或业务模块名。

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
