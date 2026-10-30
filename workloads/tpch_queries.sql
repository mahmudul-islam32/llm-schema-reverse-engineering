-- Join workload in the style of the TPC-H queries, for the TPC-H schema.
-- Use with `workload:` in a run config or `srev qlog workloads/tpch_queries.sql`.

SELECT l.l_orderkey, SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue,
              o.o_orderdate, o.o_shippriority
       FROM customer c, orders o, lineitem l
       WHERE c.c_mktsegment = 'BUILDING' AND c.c_custkey = o.o_custkey
         AND l.l_orderkey = o.o_orderkey AND o.o_orderdate < DATE '1995-03-15'
       GROUP BY l.l_orderkey, o.o_orderdate, o.o_shippriority;

SELECT n.n_name, SUM(l.l_extendedprice * (1 - l.l_discount)) AS revenue
       FROM customer c
       JOIN orders o ON c.c_custkey = o.o_custkey
       JOIN lineitem l ON l.l_orderkey = o.o_orderkey
       JOIN supplier s ON l.l_suppkey = s.s_suppkey AND c.c_nationkey = s.s_nationkey
       JOIN nation n ON s.s_nationkey = n.n_nationkey
       JOIN region r ON n.n_regionkey = r.r_regionkey
       WHERE r.r_name = 'ASIA'
       GROUP BY n.n_name;

SELECT c.c_custkey, c.c_name, n.n_name
       FROM customer c JOIN orders o ON c.c_custkey = o.o_custkey
       JOIN lineitem l ON l.l_orderkey = o.o_orderkey
       JOIN nation n ON c.c_nationkey = n.n_nationkey
       WHERE l.l_returnflag = 'R';

SELECT ps.ps_partkey, SUM(ps.ps_supplycost * ps.ps_availqty)
       FROM partsupp ps, supplier s, nation n
       WHERE ps.ps_suppkey = s.s_suppkey AND s.s_nationkey = n.n_nationkey
         AND n.n_name = 'GERMANY'
       GROUP BY ps.ps_partkey;

SELECT p.p_name, l.l_quantity, ps.ps_supplycost
       FROM part p, supplier s, lineitem l, partsupp ps
       WHERE s.s_suppkey = l.l_suppkey AND ps.ps_suppkey = l.l_suppkey
         AND ps.ps_partkey = l.l_partkey AND p.p_partkey = l.l_partkey
         AND p.p_name LIKE '%green%';

SELECT s.s_acctbal, s.s_name, n.n_name, p.p_partkey
       FROM part p, supplier s, partsupp ps, nation n, region r
       WHERE p.p_partkey = ps.ps_partkey AND s.s_suppkey = ps.ps_suppkey
         AND s.s_nationkey = n.n_nationkey AND n.n_regionkey = r.r_regionkey
         AND r.r_name = 'EUROPE';

SELECT c.c_custkey, COUNT(o.o_orderkey)
       FROM customer c LEFT OUTER JOIN orders o ON c.c_custkey = o.o_custkey
       GROUP BY c.c_custkey;

SELECT l.l_shipmode, COUNT(*) FROM orders o JOIN lineitem l
       ON o.o_orderkey = l.l_orderkey WHERE l.l_shipmode IN ('MAIL', 'SHIP')
       GROUP BY l.l_shipmode;

