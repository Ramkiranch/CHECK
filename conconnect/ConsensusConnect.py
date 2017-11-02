import pymysql.cursors
import pandas as pd
from getpass import getpass
import base64
import winsound
from helpers import PhoneMapHelper
from helpers import contactHelper
from functools import partial
from conconnect import ConsensusConnect
from helpers import PhoneMapHelper
import pymysql
from datetime import datetime
import numpy as np
import requests
import json
import time
from sqlalchemy import (MetaData, Table, Column, Integer, Numeric, String,
                        DateTime, ForeignKey, create_engine, sql)

class ConsensusConnect():

    def alertsound(self):
        for x in range(2,80):
            Freq = 800*(x//2) # Set Frequency To 2500 Hertz
            Dur = (100*x)//(x**2) # Set Duration To 1000 ms == 1 second

            winsound.Beep(Freq,Dur)
    def assessmentquery(self,assessment=None):
        '''Query for all assessments unless assessment contains a list of assessments that are needed'''

        if assessment == None:
            assessment = ''
        else:
            assessment = ", ".join([str(x) for x in assessment])
            assessment = "AND qst_questionnaire.ID in ({})".format(assessment)

        m = """
            SELECT
                pat_assessment.PatientID AS 'PatientID',
                pat_patient.MedicaidNum AS 'Medicaid ID',
                gbl_clientassessment.AssessmentName AS 'AssessmentName',
                pat_assessment.StartDate AS 'StartDate',
                pat_assessment.EndDate AS 'EndDate',
                pat_assessment.StatusCode AS 'StatusCode',
                pat_assessment.CTS AS 'Assessment CTS',
                pat_assessment.MTS AS 'Assessment MTS',
                qst_questionnairequestion.QuestionID,
                qst_question.QuestionText,
                qst_response.ResponseDate,
                qst_response.YesNo,
                qst_response.Range,
                qst_response.Text,
                qst_response.CTS AS 'Question CTS'
            FROM
                (pat_assessment
                JOIN gbl_clientassessment ON (pat_assessment.ClientAssessmentID = gbl_clientassessment.ID)
                JOIN pat_patient ON (pat_assessment.PatientID = pat_patient.ID)
                JOIN qst_questionnaire ON (gbl_clientassessment.AssessmentName = qst_questionnaire.QuestionnaireName)
                JOIN qst_questionnairequestion ON (qst_questionnaire.ID = qst_questionnairequestion.QuestionnaireID)
                JOIN qst_question ON (qst_question.ID = qst_questionnairequestion.QuestionID)
                JOIN qst_response ON (qst_response.QuestionID = qst_questionnairequestion.QuestionID
                    AND qst_response.MedRecPatientID = pat_patient.ID))
            WHERE pat_assessment.StatusCode IN ('Completed','In Process','New') {}
            AND qst_response.ResponseDate BETWEEN pat_assessment.CTS AND pat_assessment.EndDate
            ORDER BY pat_assessment.PatientID , pat_assessment.ClientAssessmentID , pat_assessment.StartDate;""".format(assessment)

        alli = self.connect(m)
        #if 'InTime Range == 2 than the question fits within the window of responses, if the question does not fit in window the
        #point is removed as it could be associated with another assessment. This would cause problems for Preassessment data as that
        #data has non coinciding dates
        alli.loc[~alli['YesNo'].isnull(),'Range'] = alli.loc[~alli['YesNo'].isnull(),'YesNo']
        #sort values for each unique question with multiple answers so most recent value is selected if the value was
        #changed, Groupby then removes the duplicates and selects the top sorted value
        alli.sort_values(['PatientID','Assessment CTS','AssessmentName','QuestionText','ResponseDate'],
                         ascending=[True,False,True,True,False],inplace=True)
        alli = alli.groupby(['PatientID','AssessmentName','QuestionText']).first()
        alli.reset_index(inplace=True)
        code_name = self.assessmentCodeValues()
        alli = pd.merge(alli,code_name,left_on=['QuestionText','Range'],right_on=['QuestionText','IntegerValue'],how='left')
        alli.drop(['IntegerValue','ID','CodeName'],axis=1,inplace=True)
        alli.loc[(alli.YesNo==0),'Response'] = 'No'
        alli.loc[(alli.YesNo==1),'Response'] = 'Yes'
        return alli

    def assessmentCodeValues(self):
        '''Assessment response encoded values '''
        n = """
            SELECT
            qst_question.QuestionText,
            qst_question.ID,
            gbl_code.CodeName,
            gbl_codevalue.Description AS Response,
            gbl_codevalue.IntegerValue
        FROM
            qst_question
                JOIN
            gbl_code ON (qst_question.OptionsGLobalCodeName = gbl_code.CodeName)
                JOIN
            gbl_codevalue ON gbl_code.CodeName = gbl_codevalue.CodeName
        WHERE
            gbl_code.CodeName LIKE 'Question %';"""
        code_name = self.connect(n)
        return code_name

    def contact(self):
        m = """
            SELECT
                pat_contact.PatientID,
                pat_patient.MedicaidNum,
                pat_contact.ContactTS,
                pat_contact.ContactTypeGBLcode,
                pat_contact.ContactPartyGBLCode,
                pat_contact.ContactReasonGBLCode,
                pat_contact.ContactCHSName,
                pat_contact.OutcomeStatusGBLCode,
                pat_contact.OutcomeDateTime,
                pat_contact.CUserID
            FROM
                pat_contact
            LEFT JOIN
                pat_patient ON pat_contact.PatientID = pat_patient.ID;"""

        alli = self.connect(m)
        contactPrecise = partial(contactHelper.contactCategorizer, precision=True)
        contactGeneral = partial(contactHelper.contactCategorizer, precision=False)
        alli['ContactTypeSuccess'] = alli['OutcomeStatusGBLCode'].apply(contactPrecise)
        alli['ContactSuccess'] = alli['OutcomeStatusGBLCode'].apply(contactGeneral)
        alli['MedicaidNum'] = alli['MedicaidNum'].apply(PhoneMapHelper.medicaidNormalizer)
        return alli

    def careplan(self):
        m = """
        SELECT
            ch.PatientID,
            pat.MedicaidNum RIN,
            ch.StartDate,
            ch.EndDate,
            ch.`Status`,
            count(pro.ProblemDescription) PendingOutComes,
            inter.InterventionDesc,
            go.GoalDescription,
            max(go.GoalDueDate) ExpirationDate
        FROM
            pat_craheader ch
                LEFT JOIN
            pat_cradiagnosis cd ON ch.ID = cd.CRAHeaderID
                LEFT JOIN
            pat_problem pro ON cd.ID = pro.CRADiagnosisID
                LEFT JOIN
            pat_intervention inter ON pro.ID = inter.ProblemID
                LEFT JOIN
            pat_goaloutcome go ON inter.ID = go.InterventionID
                LEFT JOIN
            pat_patient pat ON ch.PatientID = pat.ID
        WHERE ch.`Status` IN ('New','In Process','Completed') AND MedicaidNum IS NOT NULL
        GROUP BY cd.ID
        ORDER BY ch.PatientID ASC"""
        careplan = self.connect(m)
        careplan['RIN'] = careplan['RIN'].apply(PhoneMapHelper.medicaidNormalizer)
        return careplan

    def enrollmentStatus(self):
        m = """
        SELECT * FROM Consensus_Reporting.rpt_patient_enrollment;"""
        return self.connect(m)

    def tier1Date(self):
        m = """
            SELECT
                PatientID, `Medicaid ID`, MIN(StartDate) StartDate, StatusCode
            FROM
                Consensus_Reporting.rpt_All_Tier1
            WHERE
                StatusCode IN ('Completed', 'In Process')
                    AND
                `Medicaid ID` is not null
            GROUP BY `Medicaid ID`
            ORDER BY PatientID;
            """
        return self.connect(m,db_name='Consensus_Reporting')

    def engagementDate(self):
        m = """
            SELECT * FROM rpt_engagement_date;
            """
        return self.connect(m,db_name='Consensus_Reporting')

    def harmonyGroups(self):
        m = """
            SELECT * FROM harmony_groups;
            """
        return self.connect(m,db_name='Consensus_Reporting')

    def chwmapping(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_temp_patient_chw_mapping WHERE NetID is not NULL;"
        return self.connect(m,db_name='Consensus_Reporting')

    def chwquery(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_import_CHW_file;"
        return self.connect(m,db_name='Consensus_Reporting')

    def phonequery(self):
        m = """
        SELECT
            pphone.PatientID AS `Patient ID`,
            ppat.MedicaidNum,
            'Phone' AS Record_Type,
            'Phone' AS Party_Name,
            'Phone' AS Party_Relationship,
            'Phone' AS Party_Type,
            pphone.Description AS Phone_Type,
            CONCAT(pphone.PhoneNumberAC,
                    '-',
                    pphone.PhoneNumberPrefix,
                    '-',
                    pphone.PhoneNumberLineNumber) AS Phone_Number,
            PrimaryFlag AS Primary_Flag,
            DATE_FORMAT(pphone.StartDate,'%Y-%m-%d') AS Phone_Number_StartDate,
            DATE_FORMAT(pphone.EndDate,'%Y-%m-%d') AS Phone_Number_EndDate,
            DATE_FORMAT(pphone.CTS,'%Y-%m-%d') AS Phone_Number_Created,
            DATE_FORMAT(pphone.MTS,'%Y-%m-%d') AS Phone_Number_Updated
        FROM
            pat_phone pphone LEFT JOIN pat_patient ppat
            ON pphone.PatientID = ppat.ID;
        """
        return self.connect(m)

    def redcapImport(self,red_table='Full_CHECK',dropCol=True):
        '''Outputs the most updated redcap data: dropCol == True returns truncated df'''

        table_dict = {'Control':{'Table':'rpt_import_control_redcap',
                                'token':'89295CA57A818473318A97024F0D3CAD',
                                'ID_Col':'record_id'},
                      'Full_CHECK':{'Table':'rpt_import_redcap',
                                    'token':'BC69E6118C4782747D7E3CF7F1F6E612',
                                    'ID_Col':'rin'}}

        try:
            db = table_dict[red_table]['Table']
            token = table_dict[red_table]['token']
        except KeyError as err:
            print("{} is not a redcap table!".format(red_table))
            raise

        engine = create_engine("mysql+pymysql://gmunoz:Madison!1236@localhost:3309/Consensus_Reporting")
        conn = engine.connect()
        output_columns = ['RIN','fn','ln','gender','race_ethnicity','dob','age','address','city','state',
                          'zip_code','Risk','asthma','diabetes','scd','prematurity','newborn','epilepsy',
                          'other_diag','Diagnosis','FaerDiagnosis']

        if red_table == 'Control':
            output_columns.remove('race_ethnicity')

        redcap_old = pd.read_sql(db,conn)
        last_upload = redcap_old['upload_date'][0]
        conn.close()
        if last_upload.date() == datetime.today().date():
            #if the file was uploaded today no need to drop data set
            # and add pull from mysql
            if dropCol==True:
                redcap_old = redcap_old[output_columns]
            return redcap_old
        else:
            print('Retrieving data from the Redcap API')
            # Set the url and path to the redcap API
            content='record'
            data_format='json'
            params={'token':token,'content':content,'format':data_format}
            r = requests.post('https://erdc.hospital.uic.edu/redcap_v5.0.20/api/',data=params)
            data = r.json()
            columns = list(data[0].keys())
            redcap = pd.DataFrame(data,columns=columns)
            redcap_copy = redcap.copy()
            redcap_copy.replace('',np.nan,inplace=True)
            if red_table == 'Control':
                redcap_copy['other_diag'] = ''

            redcap_copy['zip_code'] = redcap_copy['zip_code'].apply(PhoneMapHelper.zipConvert)
            #rename columns, the identifier columns are different between tables
            redcap_copy.rename(columns={table_dict[red_table]['ID_Col']:
                                        'RIN','risk':'Risk'},inplace=True)

            redcap_copy = PhoneMapHelper.redcapDiagnosis(redcap_copy)
            redcap_copy['RIN'] = redcap_copy['RIN'].apply(PhoneMapHelper.medicaidNormalizer)
            redcap_copy['dob'] = pd.to_datetime(redcap_copy['dob'])
            redcap_copy['age'] = (datetime.today() - redcap_copy['dob']).astype('timedelta64[Y]')
            redcap_copy['upload_date'] = datetime.today()

            mco_dict = {'1':'UI Health Plus','2':'Harmony','3':'Access','4':'Meridian',
                        '5':'FHN','6':'County Care','7':'Blue Cross Blue Shield',
                        '8':'Straight Medicaid','9':'Other', '0':'N/A'}
            if red_table != 'Control':
                redcap_copy['mco_ace_type'].replace(mco_dict,inplace=True)
            conn = engine.connect()
            metadata= MetaData()
            metadata.reflect(bind=engine)
            old_table = metadata.tables[db]
            old_table.drop(engine)
            redcap_copy.to_sql(db,conn)
            if dropCol==True:
                redcap_copy = redcap_copy[output_columns]
            return redcap_copy

    def faerPatientFile(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_temp_faer_patient;"
        return self.connect(m,db_name='Consensus_Reporting')

    def actScore(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_act_scores;"
        return self.connect(m,db_name='Consensus_Reporting')

    def mhCurrentQueue(self):
        m = "SELECT * FROM Consensus_Reporting.MH_Queue;"
        return self.connect(m,db_name='Consensus_Reporting')

    def mhFaerMappingFile(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_temp_mh_faer_mapping;"
        return self.connect(m,db_name='Consensus_Reporting')

    def patLanguage(self):
        m = "SELECT * FROM Consensus_Reporting.pat_language;"
        return self.connect(m,db_name='Consensus_Reporting')

    def faerMappingFile(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_temp_faer_mapping;"
        return self.connect(m,db_name='Consensus_Reporting')

    def harmonyRand(self):
        m = "SELECT * FROM Consensus_Reporting.harmony_randomization;"
        return self.connect(m,db_name='Consensus_Reporting')

    def faerPhone(self):
        m = "SELECT * FROM Consensus_Reporting.faer_numbers;"
        return self.connect(m,db_name='Consensus_Reporting')

    def actProc(self):
        m = "act_temp"
        return self.connect(m,db_name='Consensus_Reporting',proc=True)

    def riskHistory(self):
        m = "SELECT * FROM Consensus_Reporting.risk_history;"
        return self.connect(m,db_name='Consensus_Reporting')

    def totalDemo(self,dropCol=False):
        '''Merges enrollment engagement and redcap. There are some patients with one RIN and two Patient IDs'''
        enrollment = self.enrollmentStatus()
        engagement = self.engagementDate()
        redcap = self.redcapImport()
        enrollment['RIN'] = enrollment['RIN'].apply(PhoneMapHelper.medicaidNormalizer)
        engagement['RIN'] = engagement['RIN'].apply(PhoneMapHelper.medicaidNormalizer)
        redcap['RIN'] = redcap['RIN'].apply(PhoneMapHelper.medicaidNormalizer)
        enroll_engage_merge = pd.merge(enrollment,engagement,on='RIN',how='left',suffixes=('', '_y'))
        tot_merge = pd.merge(enroll_engage_merge,redcap,on='RIN',how='left')
        tot_merge.drop(labels='PatientID_y',axis=1,inplace=True)
        tot_merge.loc[~(tot_merge['EngagementDate'].isnull()),'Patient_Type'] = 'Engaged'
        tot_merge.loc[(tot_merge['EngagementDate'].isnull())&
                      (~tot_merge['First_Enrollment_Date'].isnull()),'Patient_Type'] = 'Enrolled'
        if dropCol == True:
                tot_merge.drop(labels=['asthma','diabetes','scd','prematurity','newborn',
                'epilepsy','other_diag','SumDiagnosis','FaerDiagnosis','index'],axis=1,inplace=True)
        return tot_merge

    def connect(self,m,db_name='consensus',proc=False):
        self.connection = pymysql.connect(host='localhost',
                                 port=3309,
                                 user='gmunoz',
                                 password='Madison!1236',
                                 db=db_name,
                                 charset='utf8mb4',
                                 cursorclass=pymysql.cursors.DictCursor)
        try:
            with self.connection.cursor() as cursor:
                if proc == False:
                    cursor.execute(m)
                    result = cursor.fetchall()
                    alliDF = pd.read_sql(m,con=self.connection)
                else:
                    cursor.callproc(m)
                    alliDF = print('Success')
        finally:
            self.alertsound()
            self.connection.close()
        return alliDF