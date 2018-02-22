from datetime import datetime
from functools import partial
import numpy as np
import pandas as pd
import winsound
import time
from helpers import PhoneMapHelper
from helpers import contactHelper
from dbconnect import dbconnect

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
                qst_response.CTS AS 'Question CTS',
                qst_response.CUserID
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

        alli = self.connect(m,db_name='consensus')
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
        alli['CUserID'] = alli['CUserID'].str.lower().str.strip()
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
        code_name = self.connect(n,db_name='consensus')
        return code_name

    def claimsDx(self):
        m = """SELECT * FROM  vw_claims_dx"""
        return self.connect(m,db_name='Consensus_Reporting')

    def contact(self, pat_contacts_only=False):
        '''pat_contacts_only selects contacts that involve contact with a patient not contacts between staff'''

        if pat_contacts_only == True:
            extension = """ WHERE pat_contact.ContactPartyGBLCode IN ('Caregiver','Patient','Home')
            AND pat_contact.ContactTypeGBLcode IN ('Phone - Outbound','Visit','Home','Phone - Inbound')"""
        else:
            extension = ""

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
                pat_patient ON pat_contact.PatientID = pat_patient.ID {};""".format(extension)

        alli = self.connect(m,db_name='consensus')

        contactPrecise = partial(contactHelper.contactCategorizer, precision=True)
        contactGeneral = partial(contactHelper.contactCategorizer, precision=False)
        alli['ContactTypeSuccess'] = alli['OutcomeStatusGBLCode'].apply(contactPrecise)
        alli['ContactSuccess'] = alli['OutcomeStatusGBLCode'].apply(contactGeneral)
        alli['MedicaidNum'] = alli['MedicaidNum'].apply(PhoneMapHelper.medicaidNormalizer)

        return alli

    def careplan(self):
        return self.connect("SELECT * FROM vw_careplan",db_name='Consensus_Reporting')

    def cpsAttendance(self):
        '''returns cps_attendance table that holds all of the CPS data. table_id is equal
        to the yyyy-mm the file was given, if table_id == 'all' returns all data which is
        quite large'''
        # grade == 20 means they are part of the school only peripherally for
        # certain occupational classes but are not full time students.
        m = "Select * FROM cps_attendance_current WHERE Student_Annual_Grade_Code != 20"
        df = self.connect(m,db_name='Consensus_Reporting')
        df['Date'] = pd.to_datetime(df['Date'])

        return df

    def cpsDelta(self,table):
        '''CPS delta files to send monthly'''

        if table in ['membership','participant']:
            m = "SELECT * FROM cps_{}_delta".format(table)
            return self.connect(m,db_name='Consensus_Reporting')
        else:
            return 'not a valid cps delta table'

    def enrollmentDB(self):
        m = """SELECT * FROM  tbl_enrollment"""
        return self.connect(m,db_name='CHECK_Enrollment_DB')

    def tier1Date(self):
        m = """
            SELECT
                PatientID, RIN, StartDate, StatusCode
            FROM
                Consensus_Reporting.rpt_Tier1
            ORDER BY PatientID;
            """
        return self.connect(m,db_name='Consensus_Reporting')

    def engagementDate(self):
        m = """
            SELECT PatientID, RecipientID, Engagement_Date FROM tbl_enrollment WHERE Engagement_Date is not Null
            """
        return self.connect(m,db_name='CHECK_Enrollment_DB')

    def harmonyGroups(self):
        m = "SELECT * FROM CHECK_Enrollment_DB.vw_harmony_randomization_groups_current;;"
        return self.connect(m,db_name='CHECK_Enrollment_DB')

    def chwmapping(self):
        m = "SELECT * FROM Consensus_Reporting.live_patient_chw_mapping;"
        return self.connect(m,db_name='Consensus_Reporting')

    def chwquery(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_import_CHW_file;"
        return self.connect(m,db_name='Consensus_Reporting')

    def pharmacy(self):
        m = "SELECT * FROM tsc_hfs_pharmacy;"
        return self.connect(m,db_name="CHECK_CPAR")

    def icdDescription(self):
        m = "SELECT * FROM icd_descriptions;"
        return self.connect(m,db_name="Consensus_Reporting")

    def cpar_patient_info(self):
        m = """SELECT
                RecipientID,
                Age,
                AgeCategory,
                Gender,
                Asthma,
                Diabetes,
                SCD,
                Prematurity,
                BrainInjury,
                Epilepsy,
                TOTAL,
                DiagnosisCategory,
                ICD_list,
                RiskScore,
                RISK_TIER_NEW AS Risk,
                EnrolledFlag,
                EngagedFlag,
                controlFlag
            FROM
                tuc_hfs_check_population_info"""
        return self.connect(m,db_name="CHECK_CPAR")

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

    def faerPatientFile(self):
        m = "SELECT * FROM Consensus_Reporting.rpt_temp_faer_patient;"
        return self.connect(m,db_name='Consensus_Reporting')

    def actScore(self):
        self.callACT()
        m = "SELECT * FROM Consensus_Reporting.rpt_act_scores;"
        return self.connect(m,db_name='Consensus_Reporting')

    def patCareTeam(self):
        m = "SELECT * FROM Consensus_Reporting.live_patient_careteam;"
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

    def patientGroup(self):
        m= """
            SELECT
            RecipientID,
            E4,
            E2,
            HE4,
            HE2,
            HC,
            Program_Date
            FROM tbl_population_release
            WHERE releaseNum = (SELECT MAX(ReleaseNum) FROM tbl_population_release)"""
        df =  self.connect(m,db_name="CHECK_Enrollment_DB")
        df['Program_Date'] = pd.to_datetime(df['Program_Date'])
        return df

    def consensusRisk(self):
        m = "SELECT ID AS PatientID, SeverityLevel AS Consensus_Risk FROM pat_patient"
        return self.connect(m,db_name='consensus')

    def riskHistory(self):
        m = "SELECT * FROM Consensus_Reporting.risk_history;"
        return self.connect(m,db_name='Consensus_Reporting')

    def totalDemo(self,dropCol=False):
        '''Merges enrollment engagement and redcap. There are some patients with one RIN and two Patient IDs'''
        m = """SELECT
                te.*, cs.Current_Enrollment_Status, cs.`Group`, pg.E2, pg.E4, pg.HE2, pg.HE4, pg.Program_Date
            FROM
                CHECK_Enrollment_DB.tbl_enrollment te
                    LEFT JOIN
                CHECK_Enrollment_DB.vw_current_total_patient_status cs ON te.RecipientID = cs.RecipientID
            		LEFT JOIN
            	CHECK_Enrollment_DB.vw_current_patient_groupings pg on pg.RecipientID = te.RecipientID;"""
        df =  self.connect(m,db_name="CHECK_CPAR")
        con_risk = self.consensusRisk()
        enrollment = pd.merge(df,con_risk,on='PatientID')
        return enrollment

    def callACT(self):
        m = 'CALL act_scores();'
        return self.connect(m,db_name='Consensus_Reporting',df_flag=False)
    
    def releaseDate(self, relnum):
        m = "SELECT HFS_Release_Date FROM release_info where ReleaseNum = {}".format(relnum)
        return self.connect(m, db_name='CHECK_CPAR2')

    def connect(self,sql_str,db_name,df_flag=True):
        '''sql_str: query text to be sent to db
        db_name: str of the database query is sent to
        df_flag: Boolean to return an pandas dataframe or not'''
        connector = dbconnect.DatabaseConnect(db_name)
        try:
            if df_flag == False:
                connector.query(sql_str,df_flag=False)
                alliDF = "'{}' successfully ran".format(sql_str)
            elif df_flag == True:
                alliDF = connector.query(sql_str,df_flag=True)
        finally:
            self.alertsound()
        return alliDF
