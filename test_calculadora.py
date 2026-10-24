import unittest
from nutricao_service import calcular_refeicao

class TestCalculadoraNutricional(unittest.TestCase):

    def test_refeicao_simples(self):
        # 1. Uma refeição simples
        alimentos = [
            {"nome": "Arroz branco cozido", "quantidade": 100, "unidade": "g"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertEqual(conf, "Alta Confiança")
        self.assertAlmostEqual(totais["kcal"], 130.0, delta=5.0)

    def test_medidas_caseiras(self):
        # 2. Uma refeição com várias medidas caseiras
        alimentos = [
            {"nome": "Feijão carioca cozido", "quantidade": 1, "unidade": "concha"},
            {"nome": "Arroz branco cozido", "quantidade": 2, "unidade": "escumadeira"}
        ]
        # Concha feijao = 140g = (140/100)*76 = 106.4 kcal
        # Escumadeira arroz = 70g * 2 = 140g = (140/100)*130 = 182 kcal
        # Total = 288.4
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertEqual(conf, "Alta Confiança")
        self.assertAlmostEqual(totais["kcal"], 288.4, delta=5.0)

    def test_alimentos_gramas(self):
        # 3. Alimentos em gramas
        alimentos = [
            {"nome": "Peito de frango grelhado/cozido", "quantidade": 150, "unidade": "g"}
        ]
        # 165 kcal/100g * 1.5 = 247.5 kcal
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertEqual(conf, "Alta Confiança")
        self.assertAlmostEqual(totais["kcal"], 247.5, delta=5.0)

    def test_alimentos_ml(self):
        # 4. Alimentos em ml
        alimentos = [
            {"nome": "Leite de vaca integral", "quantidade": 250, "unidade": "ml"}
        ]
        # 60 kcal/100ml * 2.5 = 150 kcal
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertEqual(conf, "Alta Confiança")
        self.assertAlmostEqual(totais["kcal"], 150.0, delta=5.0)

    def test_produtos_industrializados_com_rotulo(self):
        # 5. Produtos industrializados (usuário fornece o rótulo)
        alimentos = [
            {
                "nome": "Whey Isolado Marca XYZ", 
                "quantidade": 1, 
                "unidade": "scoop",
                "rotulo_kcal": 120,
                "rotulo_prot": 24,
                "rotulo_carb": 3,
                "rotulo_gord": 1
            }
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertAlmostEqual(totais["kcal"], 120.0, delta=1.0)
        self.assertEqual(itens[0]["fonte"], "Fornecido pelo Usuário")

    def test_whey_banco(self):
        # 6. Whey sem rotulo (usa banco TACO/seed)
        alimentos = [
            {"nome": "Whey protein concentrado", "quantidade": 30, "unidade": "g"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertAlmostEqual(totais["kcal"], 120.0, delta=5.0)

    def test_alimento_desconhecido(self):
        # 8. Alimento desconhecido (fallback generico)
        alimentos = [
            {"nome": "Biscoito Marciano XYZ", "quantidade": 2, "unidade": "pacote"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertEqual(conf, "Estimativa Aproximada")
        self.assertEqual(itens[0]["fonte"], "Heurística Genérica")

    def test_refeicao_com_oleo(self):
        # 9. Refeicao com oleo informado
        alimentos = [
            {"nome": "Ovo de galinha frito", "quantidade": 2, "unidade": "unidade"},
            {"nome": "Óleo de soja", "quantidade": 1, "unidade": "colher de sopa"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertGreater(totais["kcal"], 216 + 70) 

    def test_estado_frito_heuristica(self):
        # 10. Refeição com alimento cru frito (adiciona óleo dinamicamente)
        alimentos = [
            {"nome": "Peito de frango", "quantidade": 1, "unidade": "bife", "estado": "frito"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
        self.assertGreater(totais["kcal"], 165.0)
        self.assertIn("(+óleo)", itens[0]["confianca"])

    def test_refeicao_especifica_exagerada(self):
        # Teste obrigatório solicitado:
        alimentos = [
            {"nome": "Leite de vaca integral", "quantidade": 200, "unidade": "ml"},
            {"nome": "Whey protein concentrado", "quantidade": 33, "unidade": "g"},
            {"nome": "Pão de forma tradicional", "quantidade": 2, "unidade": "fatia"},
            {"nome": "Queijo muçarela", "quantidade": 1, "unidade": "fatia"},
            {"nome": "Presunto cozido", "quantidade": 1, "unidade": "fatia"}
        ]
        totais, itens, conf = calcular_refeicao(alimentos)
from rag_nutricional import base_alimentos, tabela_medidas, analisar_refeicao_rag

class TestRAGNutricional(unittest.TestCase):

    def test_prioridade_cozido_vs_cru(self):
        # Quando busca "arroz", deve priorizar arroz cozido sobre cru
        matches = base_alimentos.buscar("arroz")
        nomes = [m[1]["nome"].lower() for m in matches[:3]]
        self.assertTrue(any("cozido" in n for n in nomes))
        self.assertFalse(any("cru" in n for n in nomes))

    def test_busca_cru_explicita(self):
        # Quando usuário pede explicitamente cru, arroz cru deve aparecer no topo
        matches = base_alimentos.buscar("arroz cru")
        nomes = [m[1]["nome"].lower() for m in matches[:3]]
        self.assertTrue(any("cru" in n for n in nomes))

    def test_prioridade_feijao_cozido(self):
        matches = base_alimentos.buscar("feijao")
        nomes = [m[1]["nome"].lower() for m in matches[:3]]
        self.assertTrue(any("cozido" in n for n in nomes))

    def test_conversao_medidas_caseiras_rag(self):
        # 1 escumadeira de arroz cozido
        gramas, metodo, _ = tabela_medidas.resolver("Arroz tipo 1 cozido", 1, "escumadeira")
        self.assertIsNotNone(gramas)
        self.assertAlmostEqual(gramas, 90.0, delta=5.0)

        # 1 concha de feijão
        gramas_f, _, _ = tabela_medidas.resolver("Feijão carioca cozido", 1, "concha")
        self.assertIsNotNone(gramas_f)
        self.assertAlmostEqual(gramas_f, 140.0, delta=5.0)

        # 2 fatias de pão francês (unidade ~ 50g)
        gramas_p, _, _ = tabela_medidas.resolver("Pão trigo francês", 1, "unidade")
        self.assertAlmostEqual(gramas_p, 50.0, delta=2.0)

    def test_pipeline_deterministico_rag(self):
        # Mock de retorno da IA para testar cálculo determinístico em Python
        def mock_ia(*args, **kwargs):
            return {
                "itens": [
                    {
                        "alimento_informado": "arroz branco",
                        "alimento_base": "Arroz tipo 1 cozido",
                        "quantidade": 2,
                        "unidade": "colher de sopa",
                        "gramas_estimados": 50
                    },
                    {
                        "alimento_informado": "feijão carioca",
                        "alimento_base": "Feijão carioca cozido",
                        "quantidade": 1,
                        "unidade": "concha",
                        "gramas_estimados": 140
                    }
                ],
                "fibras_g": 10,
                "dica_ia": "Refeição balanceada.",
                "qualidade_vegetais": "razoavel",
                "qualidade_proteinas": "boa",
                "qualidade_fibras": "boa",
                "qualidade_acucar": "boa",
                "qualidade_ultraprocessados": "boa"
            }

        resultado = analisar_refeicao_rag(
            descricao="2 colheres de arroz e 1 concha de feijão",
            tipo="Almoço",
            usuario={"nome": "Teste", "sexo": "masculino"},
            chamar_ia=mock_ia
        )

        self.assertGreater(resultado["total_kcal"], 150)
        self.assertLess(resultado["total_kcal"], 250)
        self.assertEqual(len(resultado["itens_detalhados"]), 2)
        self.assertEqual(resultado["nivel_confianca"], "Alta")

if __name__ == '__main__':
    unittest.main()
