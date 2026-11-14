"""Exercise the unchanged RAG decisions with mocked retrieval and Groq."""
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('GROQ_API_KEY','offline-placeholder')
from pipeline import orchestrator as pipeline
from pipeline.answer_verifier import validate_citations, validate_paragraph_citations
from pipeline.groq_client import GroqRequestError, quota_reset

PASSAGE={'paper':'paper.pdf','page':3,'text':'Supported technical evidence','score':.8}
ANSWER='Supported answer [paper.pdf, p. 3].'


class PipelineRegressionTests(unittest.TestCase):
    def setUp(self):
        retriever=Mock()
        retriever.search.return_value=[PASSAGE]
        patcher=patch.object(pipeline,'get_retriever',return_value=retriever)
        patcher.start(); self.addCleanup(patcher.stop)
        self.retriever=retriever

    @patch.object(pipeline,'verify_answer',return_value={'verified':True,'reason':'Supported'})
    @patch.object(pipeline,'generate_answer',return_value={'content':ANSWER,'tokens_in':10,'tokens_out':5,'api_retries':1})
    def test_success_and_cited_only_sources(self,generator,verifier):
        result=pipeline.run_pipeline('Question')
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['sources'],[{'paper':'paper.pdf','page':3}])
        self.assertEqual(result['metrics']['retries'],0)
        self.assertEqual(result['metrics']['api_retries'],1)
        self.retriever.search.assert_called_once_with('Question',top_k=8,setting='R4',candidate_k=50)

    @patch.object(pipeline,'generate_answer',return_value={'content':ANSWER})
    @patch.object(pipeline,'verify_answer',side_effect=[{'verified':False,'reason':'Fix citation'},{'verified':True,'reason':'Supported'}])
    def test_one_correction(self,verify,generate):
        result=pipeline.run_pipeline('Question')
        self.assertEqual(result['status'],'ok')
        self.assertEqual(generate.call_count,2)
        self.assertEqual(result['metrics']['retries'],1)
        self.assertEqual(generate.call_args.kwargs['correction'],'Fix citation')

    @patch.object(pipeline,'generate_answer',return_value={'content':ANSWER})
    @patch.object(pipeline,'verify_answer',return_value={'verified':False,'reason':'Unsupported'})
    def test_verification_refusal(self,verify,generate):
        result=pipeline.run_pipeline('Question')
        self.assertEqual(result['status'],'verification_failed')
        self.assertEqual(generate.call_count,2)

    @patch.object(pipeline,'generate_answer')
    def test_low_confidence_refuses_without_provider(self,generate):
        self.retriever.search.return_value=[{**PASSAGE,'score':.1}]
        self.assertEqual(pipeline.run_pipeline('Question', configuration={'min_score':.25})['status'],'refused_correctly')
        generate.assert_not_called()
        self.assertEqual(pipeline.run_pipeline(' ')['status'],'invalid')

    @patch.object(pipeline,'generate_answer',side_effect=GroqRequestError('TPD exhausted',category='daily_quota_exhausted',status=429))
    def test_api_error_remains_distinct(self,generate):
        result=pipeline.run_pipeline('Question')
        self.assertEqual(result['status'],'api_error')
        self.assertEqual(result['error']['category'],'daily_quota_exhausted')

    def test_citation_validation(self):
        self.assertTrue(validate_citations(ANSWER,[PASSAGE])[0])
        self.assertFalse(validate_citations('Wrong [paper.pdf, p. 4].',[PASSAGE])[0])
        self.assertFalse(validate_paragraph_citations(ANSWER+'\n\nUncited paragraph.')[0])

    def test_daily_does_not_use_minute_reset(self):
        self.assertIsNone(quota_reset({'message':'Tokens per day (TPD) exhausted','reset_tokens':'2s'}))
        self.assertIsNotNone(quota_reset({'message':'Tokens per day (TPD) exhausted. Please try again in 2h3m4s.'}))


if __name__=='__main__':
    unittest.main()
