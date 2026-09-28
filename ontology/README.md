# 本体目录

本目录保存股票本体MVP的语义资产。所有文件进入Git版本控制，并使用语义化版本管理。

## 文件

```text
ontology/
├── stock-core.ttl          核心实体、关系和Claim模型
├── stock-relations.ttl     组成、收入证据、否认和主题相关性关系扩展
├── product-skos.ttl        产品与产业链SKOS词表
├── shapes.ttl              SHACL数据质量约束
├── rules.yaml              可执行推理规则定义
└── mappings/
    └── tushare.yaml        TuShare字段到领域模型的映射
```

## 设计原则

- OWL描述类、属性、Domain、Range和基础语义。
- SKOS管理产品标准名、别名、上下位和相关关系。
- SHACL负责数据进入事实库和图谱前的确定性校验。
- 复杂经营判断使用版本化规则，不把所有逻辑写入OWL推理。
- 平台概念标签、经营事实和推理事实使用不同谓词和`factLayer`。
- 本体IRI稳定；显示名称可以修改，IRI不得随中文名称变化。
- 原始披露名称保留在数据层，通过映射表连接标准产品概念。
- `rules.yaml`引用的关系必须在`stock-core.ttl`或`stock-relations.ttl`中显式声明。

## 命名空间

MVP使用占位命名空间：

```text
https://ontology.example.com/stock#
https://ontology.example.com/product#
```

正式部署前替换为企业控制的稳定域名。替换后不能随环境变化；开发、测试和生产使用相同IRI。

建议前缀：

```text
stock:   https://ontology.example.com/stock#
product: https://ontology.example.com/product#
prov:    http://www.w3.org/ns/prov#
skos:    http://www.w3.org/2004/02/skos/core#
sh:      http://www.w3.org/ns/shacl#
```

## 版本治理

本体版本采用`MAJOR.MINOR.PATCH`：

- MAJOR：不兼容的类、属性或语义变化。
- MINOR：新增兼容类、属性、产品节点或规则。
- PATCH：标签、说明、约束缺陷修复。

每个Claim保存`ontology_version`。查询结果返回所用本体版本。

## 变更流程

1. 提交本体或词表变更PR。
2. 执行TTL语法、OWL一致性、SHACL和黄金查询测试。
3. 输出影响分析：受影响Claim、产品映射和查询。
4. 至少一名领域审核人批准。
5. 发布新版本。
6. 运行必要的数据迁移和Neo4j重建。

## 质量门禁

- 类和属性必须有标签和说明。
- ObjectProperty必须定义合理的Domain和Range。
- 产品概念必须有唯一`skos:prefLabel`。
- 别名不得在同一上下文映射到多个Accepted产品，除非进入人工消歧。
- 不能出现分类环。
- SHACL错误数必须为0。
- 孤立类、孤立属性和未解析关系端点需要告警。
- 规则文件引用的类、关系、阶段和证据状态必须能够解析。

## Semantica集成

Semantica通过`OntologyEngine`加载、验证和导出本体，通过`OntologyQualityGate`执行CI质量门禁。业务代码不得直接依赖Semantica内部本体对象，统一由项目的`SemanticRuntime`适配层封装。
