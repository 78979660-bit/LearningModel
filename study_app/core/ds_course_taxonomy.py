from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DSTopic:
    chapter: str
    topic: str
    template_id: str
    description: str
    keywords: tuple[str, ...]


DS_CHAPTERS = (
    "第1章：绪论",
    "第2章：线性表",
    "第3章：栈和队列",
    "第4章：数组、串与广义表",
    "第5章：树",
    "第6章：集合与字典",
    "第7章：搜索结构",
    "第8章：图",
    "第9章：排序",
)


DS_TOPICS = (
    DSTopic("第1章：绪论", "数据结构基本概念与抽象数据类型", "DS-INTRO-ADT", "数据结构、逻辑与存储结构、抽象数据类型及算法描述", ("抽象数据类型", "ADT", "逻辑结构", "存储结构")),
    DSTopic("第1章：绪论", "算法复杂度分析", "ALG-COMPLEXITY", "循环与递归复杂度、最好最坏平均情形、渐进记号", ("复杂度", "Big-O", "时间复杂度", "空间复杂度", "递归树", "主定理", "嵌套循环", "循环", "递推", "摊还", "期望比较", "逆序对", "输出敏感", "最好最坏", "平均情形")),
    DSTopic("第2章：线性表", "顺序表", "DS-LIST-OPS", "线性表顺序存储、插入删除、查找及复杂度", ("顺序表", "线性表", "顺序存储", "dynamic array", "array list")),
    DSTopic("第2章：线性表", "单链表、循环链表与双向链表", "DS-LIST-OPS", "链表构造、插入删除、指针调整与边界情况", ("单链表", "循环链表", "双向链表", "链表", "linked list", "doubly linked", "singly linked")),
    DSTopic("第3章：栈和队列", "栈及其应用", "DS-STACK-QUEUE", "栈的实现、表达式处理、括号匹配与应用", ("栈", "stack", "括号匹配", "表达式", "parentheses matching")),
    DSTopic("第3章：栈和队列", "队列及其应用", "DS-STACK-QUEUE", "队列、循环队列、链式队列及应用", ("队列", "循环队列", "queue", "deque")),
    DSTopic("第3章：栈和队列", "递归与非递归转换", "DS-STACK-QUEUE", "递归过程、递归栈与非递归算法转换", ("递归", "非递归")),
    DSTopic("第4章：数组、串与广义表", "多维数组、特殊矩阵与稀疏矩阵", "DS-ARRAY-SPARSE", "数组地址计算、特殊矩阵压缩存储与稀疏矩阵", ("多维数组", "特殊矩阵", "稀疏矩阵", "压缩存储", "地址计算", "sparse matrix", "multidimensional array")),
    DSTopic("第4章：数组、串与广义表", "串与 KMP 算法", "ALG-KMP-PREFIX", "串的基本操作、next 数组、失配跳转与 KMP 匹配", ("KMP", "字符串", "串", "next数组", "next 数组")),
    DSTopic("第4章：数组、串与广义表", "广义表", "DS-GENERALIZED-LIST", "广义表结构、深度长度、表头表尾与递归操作", ("广义表",)),
    DSTopic("第5章：树", "树、森林与二叉树", "DS-TREE-BINARY", "树与森林、二叉树性质、存储、遍历与线索化", ("树与森林", "二叉树", "二叉树遍历", "线索二叉树", "树的遍历", "binary tree", "tree traversal", "preorder", "inorder", "postorder")),
    DSTopic("第5章：树", "堆与优先队列", "DS-HEAP-HUFFMAN", "堆的构造调整、优先队列与应用", ("优先队列", "堆", "heap", "priority queue")),
    DSTopic("第5章：树", "哈夫曼树与编码", "DS-HEAP-HUFFMAN", "哈夫曼树构造、带权路径长度与编码", ("哈夫曼", "Huffman", "带权路径长度", "prefix code")),
    DSTopic("第6章：集合与字典", "等价类与并查集", "DS-UNION-FIND", "等价关系、并查集、路径压缩与按秩合并", ("并查集", "等价类", "Union-Find", "union find", "disjoint set", "路径压缩")),
    DSTopic("第6章：集合与字典", "字典与哈希表", "DS-HASH-ASL", "字典、哈希表构造、冲突处理、装填因子及平均查找长度", ("哈希", "散列", "字典", "装填因子", "平均查找长度", "二次探测", "开放定址")),
    DSTopic("第7章：搜索结构", "静态搜索结构", "DS-STATIC-SEARCH", "顺序查找、折半查找、判定树与查找性能", ("静态搜索", "顺序查找", "折半查找", "二分查找", "判定树", "binary search")),
    DSTopic("第7章：搜索结构", "二叉搜索树", "DS-BST-OPS", "二叉搜索树插入、删除、遍历、判定与查找性能", ("二叉搜索树", "BST")),
    DSTopic("第7章：搜索结构", "AVL 树旋转与插入删除", "DS-AVL-ROT", "AVL 插入删除、平衡因子、失衡回溯与旋转", ("AVL", "平衡因子", "旋转类型")),
    DSTopic("第8章：图", "图的存储与遍历", "DS-GRAPH-TRAVERSAL", "图的概念、邻接矩阵/表、DFS 与 BFS", ("图的存储", "邻接矩阵", "邻接表", "DFS", "BFS", "深度优先", "广度优先", "graph traversal", "breadth first", "depth first", "adjacency")),
    DSTopic("第8章：图", "最小生成树与最短路径", "DS-GRAPH-MST-SP", "Prim/Kruskal 最小生成树与单源/多源最短路径", ("最小生成树", "Prim", "Kruskal", "最短路径", "Dijkstra", "Floyd")),
    DSTopic("第8章：图", "拓扑排序与关键路径", "DS-GRAPH-DAG", "有向无环图、拓扑排序、AOE 网与关键路径", ("拓扑排序", "关键路径", "AOE", "DAG", "topological", "critical path")),
    DSTopic("第9章：排序", "插入排序与希尔排序", "DS-SORTING", "直接/折半插入排序与希尔排序过程和性能", ("插入排序", "希尔排序")),
    DSTopic("第9章：排序", "交换排序与快速排序", "DS-SORTING", "冒泡排序、快速排序过程、划分与性能", ("交换排序", "冒泡排序", "快速排序", "快排")),
    DSTopic("第9章：排序", "选择排序与堆排序", "DS-SORTING", "选择排序、堆排序构造调整与性能", ("选择排序", "堆排序")),
    DSTopic("第9章：排序", "归并、基数与外部排序", "DS-SORTING", "归并排序、基数排序、外部排序及性能比较", ("归并排序", "基数排序", "外部排序", "外排序")),
)


TEMPLATE_INFO = {
    item.template_id: ("数据结构与算法基础", f"{item.chapter} / {item.topic}", item.description)
    for item in DS_TOPICS
}

CHAPTER_KEYWORDS = {
    1: ("第1章", "第一章"),
    2: ("第2章", "第二章"),
    3: ("第3章", "第三章"),
    4: ("第4章", "第四章"),
    5: ("第5章", "第五章"),
    6: ("第6章", "第六章", "宁-第6章"),
    7: ("第7章", "第七章"),
    8: ("第8章", "第八章"),
    9: ("第9章", "第九章"),
}


def classify_ds_text(text: str) -> DSTopic | None:
    normalized = (text or "").lower()
    matches = [
        (sum(len(keyword) for keyword in item.keywords if keyword.lower() in normalized), item)
        for item in DS_TOPICS
    ]
    matches = [match for match in matches if match[0] > 0]
    return max(matches, key=lambda match: match[0])[1] if matches else None


def chapter_from_text(text: str) -> str | None:
    normalized = text or ""
    for number, keywords in CHAPTER_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return DS_CHAPTERS[number - 1]
    return None


def ds_template_ids() -> set[str]:
    return set(TEMPLATE_INFO)
